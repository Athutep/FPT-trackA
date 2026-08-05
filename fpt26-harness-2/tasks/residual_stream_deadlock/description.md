Kernel Description:
`residual` applies a streaming residual (skip) connection over a length-`N` array:
for each element, `out[i] = 2 * in[i] + in[i]`. It is structured as a Vitis HLS
`#pragma HLS DATAFLOW` region of three tasks connected by `hls::stream` FIFOs:

- `stageA`: reads `in[]`, and drives two streams — a "main" path and a "skip" path.
- `stageB`: reads the main stream, computes `2 * x`, writes it downstream.
- `stageC`: reads the computed main stream and the skip stream, and writes
  `out[i] = main[i] + skip[i]`.

Top-Level Function: `residual`

Complete Function Signature:
`void residual(data_t in[N], data_t out[N]);`

Data types / constants (header, do not change): `data_t = int`, `N = 64`.

Numerical contract: `out[i] == 2*in[i] + in[i]` for all `i`.

Initial condition:
The provided implementation **passes C-simulation** but **deadlocks in C/RTL
co-simulation**. This is a structural streaming hazard, not a numerical bug: the
producer emits one stream in full before the other, which unbounded C-sim FIFOs
tolerate but bounded RTL FIFOs cannot. Diagnose it from the co-simulation
feedback and restructure the dataflow so the design co-simulates without
deadlock, preserving `out[i] = 2*in[i] + in[i]`.
