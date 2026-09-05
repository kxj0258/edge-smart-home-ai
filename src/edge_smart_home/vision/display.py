from __future__ import annotations

from typing import Iterable

from .interfaces import GestureObservation


# MediaPipe's standard hand topology, using the 21 landmark indexes.
HAND_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)


def landmark_pixel_points(
    observation: GestureObservation,
    width: int,
    height: int,
    *,
    mirror: bool = False,
    margin: int = 20,
) -> tuple[tuple[int, int], ...]:
    """Map normalized landmarks to pixels for a display canvas.

    The live demo passes the already mirrored camera frame to MediaPipe, so it
    uses the default ``mirror=False`` when drawing.  The option is useful for
    callers that draw landmarks on an unmirrored source frame.
    """
    if width <= 2 * margin or height <= 2 * margin:
        raise ValueError("display dimensions must be larger than twice the margin")
    points = []
    for x, y, _ in observation.landmarks:
        display_x = 1.0 - x if mirror else x
        points.append(
            (
                margin + round(display_x * (width - 2 * margin)),
                margin + round(y * (height - 2 * margin)),
            )
        )
    return tuple(points)


def valid_hand_observations(
    observations: Iterable[GestureObservation],
) -> tuple[GestureObservation, ...]:
    """Return only observations carrying a complete MediaPipe hand skeleton."""
    return tuple(observation for observation in observations if len(observation.landmarks) == 21)
