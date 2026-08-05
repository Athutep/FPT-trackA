Kernel Description:
`dotProduct` computes the dot product of two length-`NUM_FEATURES` fixed-point
vectors, `param` and `feature`, returning a single fixed-point scalar
`sum(param[i] * feature[i])`.

Top-Level Function: `dotProduct`

Complete Function Signature:
`FeatureType dotProduct(FeatureType param[NUM_FEATURES], DataType feature[NUM_FEATURES]);`

Inputs:
- `param`:   array of `NUM_FEATURES` `FeatureType` (`ap_fixed<32,13>`) values.
- `feature`: array of `NUM_FEATURES` `DataType` (`ap_fixed<16,4>`) values.

Output:
- returns the accumulated dot product as a `FeatureType`.

Constants (in the header, do not change):
- `NUM_FEATURES = 1024`, `PAR_FACTOR = 32`.

Numerical tolerance: the testbench accepts an absolute error up to 1e-2 versus a
float reference (fixed-point rounding is expected).

Initial condition:
The provided implementation is functionally correct but UNOPTIMIZED — a single
sequential accumulation loop with no HLS pragmas, so its synthesized latency is
high. Your goal is to reduce latency (while keeping it correct and synthesizable)
on the target Alveo U55C at 200 MHz.
