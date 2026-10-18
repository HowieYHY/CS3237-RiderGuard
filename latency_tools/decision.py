OBJECT_THRESHOLD_CM = 200
SIDE_MARGIN_CM = 30


def object_hazard(distance_cm):
    return distance_cm < OBJECT_THRESHOLD_CM


def lane_split_ok(left_cm, right_cm):
    return left_cm >= SIDE_MARGIN_CM and right_cm >= SIDE_MARGIN_CM
