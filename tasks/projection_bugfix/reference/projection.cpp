#include "projection.h"

// Golden correct reference.
void projection(Triangle_3D triangle_3d, Triangle_2D *triangle_2d, bit2 angle) {
    if (angle == 0) {
        triangle_2d->x0 = triangle_3d.x0;
        triangle_2d->y0 = triangle_3d.y0;
        triangle_2d->x1 = triangle_3d.x1;
        triangle_2d->y1 = triangle_3d.y1;
        triangle_2d->x2 = triangle_3d.x2;
        triangle_2d->y2 = triangle_3d.y2;
        triangle_2d->z =
            triangle_3d.z0 / 3 + triangle_3d.z1 / 3 + triangle_3d.z2 / 3;
    }

    else if (angle == 1) {
        triangle_2d->x0 = triangle_3d.x0;
        triangle_2d->y0 = triangle_3d.z0;
        triangle_2d->x1 = triangle_3d.x1;
        triangle_2d->y1 = triangle_3d.z1;
        triangle_2d->x2 = triangle_3d.x2;
        triangle_2d->y2 = triangle_3d.z2;
        triangle_2d->z =
            triangle_3d.y0 / 3 + triangle_3d.y1 / 3 + triangle_3d.y2 / 3;
    }

    else if (angle == 2) {
        triangle_2d->x0 = triangle_3d.z0;
        triangle_2d->y0 = triangle_3d.y0;
        triangle_2d->x1 = triangle_3d.z1;
        triangle_2d->y1 = triangle_3d.y1;
        triangle_2d->x2 = triangle_3d.z2;
        triangle_2d->y2 = triangle_3d.y2;
        triangle_2d->z =
            triangle_3d.x0 / 3 + triangle_3d.x1 / 3 + triangle_3d.x2 / 3;
    }
}
