Kernel Description:
The `projection` kernel projects a 3D triangle onto a 2D plane given a projection
`angle` (0, 1, or 2). Each 2D vertex's x/y are selected from the 3D vertex
coordinates according to the angle, and the 2D triangle's single `z` field is the
AVERAGE of the three 3D vertices' relevant coordinate (each divided by 3 and
summed). Camera at (0,0,-1), canvas at z=0.

Top-Level Function: `projection`

Complete Function Signature:
`void projection(Triangle_3D triangle_3d, Triangle_2D *triangle_2d, bit2 angle);`

Inputs:
- `triangle_3d`: a `Triangle_3D` struct (nine `bit8` fields x0..z2).
- `triangle_2d`: output pointer to a `Triangle_2D` struct.
- `angle`: `bit2`, one of 0, 1, 2.

Behavior per angle:
- angle 0: 2D (x,y) = 3D (x,y); z = (z0 + z1 + z2)/3  (each term /3).
- angle 1: 2D (x,y) = 3D (x,z); z = (y0 + y1 + y2)/3.
- angle 2: 2D (x,y) = 3D (z,y); z = (x0 + x1 + x2)/3.

Data types (header, do not change): `bit8 = ap_uint<8>`, `bit2 = ap_uint<2>`.

Initial condition:
The provided implementation COMPILES and synthesizes but FAILS C-simulation: it
has a functional bug in the `angle == 0` branch. Diagnose it from the testbench
feedback and return a corrected kernel.
