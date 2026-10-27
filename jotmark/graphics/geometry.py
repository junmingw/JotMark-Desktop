import math
import cv2
import numpy as np
from PySide6.QtCore import QPointF


def point_to_segment_dist(pt, p1, p2):
    """计算点到线段的距离和投影点
    
    Args:
        pt: 点
        p1: 线段起点
        p2: 线段终点
        
    Returns:
        (距离, 投影点)
    """
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    if dx == 0 and dy == 0:
        return ((pt.x() - p1.x()) ** 2 + (pt.y() - p1.y()) ** 2) ** 0.5, p1
    
    t = ((pt.x() - p1.x()) * dx + (pt.y() - p1.y()) * dy) / (dx ** 2 + dy ** 2)
    t = max(0, min(1, t))
    proj_x = p1.x() + t * dx
    proj_y = p1.y() + t * dy
    proj = QPointF(proj_x, proj_y)
    dist = ((pt.x() - proj_x) ** 2 + (pt.y() - proj_y) ** 2) ** 0.5
    return dist, proj


def point_in_polygon(pt, polygon):
    """判断点是否在多边形内（射线法）
    
    Args:
        pt: 点
        polygon: 多边形顶点列表
        
    Returns:
        bool: 点是否在多边形内
    """
    x, y = pt.x(), pt.y()
    inside = False
    n = len(polygon)
    
    for i in range(n):
        j = (i + 1) % n
        xi, yi = polygon[i].x(), polygon[i].y()
        xj, yj = polygon[j].x(), polygon[j].y()
        
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
    
    return inside


def find_closest_edge(pt, polygon):
    """找到多边形上距离点最近的边
    
    Args:
        pt: 点
        polygon: 多边形顶点列表
        
    Returns:
        (最小距离, 最近边的索引, 投影点)
    """
    min_dist = float('inf')
    closest_edge = -1
    closest_proj = None
    
    n = len(polygon)
    for i in range(n):
        j = (i + 1) % n
        dist, proj = point_to_segment_dist(pt, polygon[i], polygon[j])
        if dist < min_dist:
            min_dist = dist
            closest_edge = i
            closest_proj = proj
    
    return min_dist, closest_edge, closest_proj


def snap_to_edge(pt, polygon, threshold=8):
    """边缘吸附：如果点靠近多边形边缘，则吸附到边缘
    
    Args:
        pt: 点
        polygon: 多边形顶点列表
        threshold: 吸附阈值（像素）
        
    Returns:
        (是否吸附, 吸附后的点)
    """
    min_dist, edge_idx, proj = find_closest_edge(pt, polygon)
    if min_dist < threshold:
        return True, proj
    return False, pt


def polygon_area(polygon):
    """计算多边形面积
    
    Args:
        polygon: 多边形顶点列表
        
    Returns:
        面积
    """
    area = 0.0
    n = len(polygon)
    
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i].x() * polygon[j].y() - polygon[j].x() * polygon[i].y()
    
    return abs(area) / 2.0


def polygon_centroid(polygon):
    """计算多边形重心
    
    Args:
        polygon: 多边形顶点列表
        
    Returns:
        重心点
    """
    area = polygon_area(polygon)
    if area == 0:
        return QPointF(0, 0)
    
    cx, cy = 0.0, 0.0
    n = len(polygon)
    
    for i in range(n):
        j = (i + 1) % n
        factor = polygon[i].x() * polygon[j].y() - polygon[j].x() * polygon[i].y()
        cx += (polygon[i].x() + polygon[j].x()) * factor
        cy += (polygon[i].y() + polygon[j].y()) * factor
    
    cx /= 6.0 * area
    cy /= 6.0 * area
    return QPointF(cx, cy)


def normalize_obb_angle(angle_degrees):
    """Normalize an OBB long-axis angle to [-90, 90)."""
    return (float(angle_degrees) + 90.0) % 180.0 - 90.0


def angle_distance_180(first, second):
    """Smallest orientation difference where 0° and 180° are equivalent."""
    return abs(normalize_obb_angle(float(first) - float(second)))


def obb_corners(cx, cy, width, height, angle_degrees):
    """Return four OBB corners in scene/image coordinates."""
    radians = math.radians(float(angle_degrees))
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    half_w, half_h = max(0.0, float(width)) / 2.0, max(0.0, float(height)) / 2.0
    corners = []
    for local_x, local_y in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
        corners.append((
            float(cx) + local_x * cos_a - local_y * sin_a,
            float(cy) + local_x * sin_a + local_y * cos_a,
        ))
    return corners


def fit_obb_to_bounds(cx, cy, width, height, angle_degrees, bounds):
    """Keep a rectangle valid while preserving its target-centered placement.

    ``bounds`` can be a QRectF or ``(left, top, width, height)``.  This never
    clips the four corners independently, so the result remains a true OBB.
    The preferred correction is a centered proportional shrink, not a large
    translation: an AI-generated OBB near an image edge should stay attached
    to the selected object.  A final small translation only handles a center
    that was already outside the image or unavoidable minimum-size rounding.
    """
    if hasattr(bounds, "left"):
        left, top = float(bounds.left()), float(bounds.top())
        right, bottom = float(bounds.right()), float(bounds.bottom())
    else:
        left, top, bound_width, bound_height = map(float, bounds)
        right, bottom = left + bound_width, top + bound_height

    width, height = max(1.0, float(width)), max(1.0, float(height))
    angle_degrees = normalize_obb_angle(angle_degrees)

    def bounds_for(center_x, center_y, box_w, box_h):
        corners = obb_corners(center_x, center_y, box_w, box_h, angle_degrees)
        xs, ys = zip(*corners)
        return min(xs), min(ys), max(xs), max(ys)

    # Do not allow an invalid AI center to remain outside the canvas, but do
    # not otherwise shift the target away from the user's prompt.
    cx = max(left, min(float(cx), right))
    cy = max(top, min(float(cy), bottom))

    # Find the largest uniform scale that keeps every rotated corner inside
    # while holding the OBB center fixed.  It preserves angle/aspect ratio and
    # avoids the visually surprising "box slid away from object" behavior.
    scale = 1.0
    for offset_x, offset_y in obb_corners(0.0, 0.0, width, height, angle_degrees):
        if offset_x > 0:
            scale = min(scale, (right - cx) / offset_x)
        elif offset_x < 0:
            scale = min(scale, (left - cx) / offset_x)
        if offset_y > 0:
            scale = min(scale, (bottom - cy) / offset_y)
        elif offset_y < 0:
            scale = min(scale, (top - cy) / offset_y)
    scale = max(0.0, min(1.0, scale))
    if scale < 1.0:
        # A 1 px lower bound maintains a usable graphics item.  The final
        # residual translation below is typically sub-pixel.
        width, height = max(1.0, width * scale), max(1.0, height * scale)

    min_x, min_y, max_x, max_y = bounds_for(cx, cy, width, height)
    if min_x < left:
        cx += left - min_x
    elif max_x > right:
        cx += right - max_x
    if min_y < top:
        cy += top - min_y
    elif max_y > bottom:
        cy += bottom - max_y
    return float(cx), float(cy), float(width), float(height), float(angle_degrees)


def _long_axis_obb(rect):
    (cx, cy), (width, height), angle = rect
    width, height, angle = float(width), float(height), float(angle)
    if height > width:
        width, height = height, width
        angle += 90.0
    return float(cx), float(cy), width, height, normalize_obb_angle(angle)


def stable_obb_from_contour(contour, min_aspect_ratio=1.5, max_angle_delta=20.0):
    """Build an OBB from minAreaRect, using PCA only to stabilize long objects.

    For near-square masks minAreaRect remains authoritative.  For elongated
    masks whose PCA and minAreaRect directions agree, the PCA direction is
    used with projected contour extents so the resulting rectangle still fully
    encloses the contour.
    """
    min_area = _long_axis_obb(cv2.minAreaRect(contour))
    cx, cy, width, height, min_angle = min_area
    if width <= 1e-6 or height <= 1e-6 or width / height < min_aspect_ratio:
        return min_area

    points = contour.reshape(-1, 2).astype(np.float64)
    if len(points) < 3:
        return min_area
    centered = points - points.mean(axis=0)
    covariance = np.cov(centered, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    principal = eigenvectors[:, int(np.argmax(eigenvalues))]
    pca_angle = normalize_obb_angle(math.degrees(math.atan2(principal[1], principal[0])))
    if angle_distance_180(pca_angle, min_angle) > max_angle_delta:
        return min_area

    axis_u = np.array([math.cos(math.radians(pca_angle)), math.sin(math.radians(pca_angle))])
    axis_v = np.array([-axis_u[1], axis_u[0]])
    projection_u = points @ axis_u
    projection_v = points @ axis_v
    u0, u1 = float(projection_u.min()), float(projection_u.max())
    v0, v1 = float(projection_v.min()), float(projection_v.max())
    pca_width, pca_height = u1 - u0, v1 - v0
    if pca_width <= 1e-6 or pca_height <= 1e-6:
        return min_area
    center = axis_u * ((u0 + u1) / 2.0) + axis_v * ((v0 + v1) / 2.0)
    if pca_height > pca_width:
        pca_width, pca_height = pca_height, pca_width
        pca_angle = normalize_obb_angle(pca_angle + 90.0)
    return float(center[0]), float(center[1]), float(pca_width), float(pca_height), float(pca_angle)


def mask_to_annotation_geometry(mask, seed_point=None):
    """Clean a SAM mask and derive polygon, axis-aligned box, and stable OBB.

    A clicked component is preferred when a seed lies on it.  Small detached
    islands are removed; a close/open pass stabilizes mask edges before contour
    fitting.  The returned OBB follows the hybrid minAreaRect/PCA policy.
    """
    mask_array = np.squeeze(np.asarray(mask))
    if mask_array.ndim != 2:
        return [], [], []
    binary = (mask_array > 0.5).astype(np.uint8)
    if not binary.any():
        return [], [], []

    kernel = np.ones((3, 3), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel)
    if not cleaned.any():
        cleaned = binary

    count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    chosen = 0
    if seed_point is not None:
        sx, sy = int(round(seed_point[0])), int(round(seed_point[1]))
        if 0 <= sy < labels.shape[0] and 0 <= sx < labels.shape[1]:
            chosen = int(labels[sy, sx])
    if chosen == 0 and count > 1:
        chosen = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if chosen == 0:
        return [], [], []
    component = (labels == chosen).astype(np.uint8) * 255

    # Keep dense contour samples for PCA; polygon export is simplified below.
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return [], [], []
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 1.0:
        return [], [], []
    epsilon = max(0.75, 0.002 * cv2.arcLength(contour, True))
    approx = cv2.approxPolyDP(contour, epsilon, True)
    x, y, width, height = cv2.boundingRect(contour)
    cx, cy, obb_width, obb_height, angle = stable_obb_from_contour(contour)
    return approx.reshape(-1, 2).tolist(), [float(x), float(y), float(width), float(height)], [cx, cy, obb_width, obb_height, angle]
