#include "residual.h"
#include "hls_stream.h"

// Golden fix: stage A writes the main and skip streams in the SAME loop, so the
// two paths stay rate-balanced and bounded RTL FIFOs never overflow -> no
// deadlock. Functionally identical: out[i] = 2*in[i] + in[i].
static void stageA(data_t in[N], hls::stream<data_t> &s_main,
                   hls::stream<data_t> &s_skip) {
    for (int i = 0; i < N; i++) {
        s_main.write(in[i]);
        s_skip.write(in[i]);
    }
}

static void stageB(hls::stream<data_t> &s_main, hls::stream<data_t> &s_f) {
    for (int i = 0; i < N; i++) s_f.write(s_main.read() * 2);
}

static void stageC(hls::stream<data_t> &s_f, hls::stream<data_t> &s_skip,
                   data_t out[N]) {
    for (int i = 0; i < N; i++) out[i] = s_f.read() + s_skip.read();
}

void residual(data_t in[N], data_t out[N]) {
#pragma HLS DATAFLOW
    hls::stream<data_t> s_main, s_f, s_skip;
    stageA(in, s_main, s_skip);
    stageB(s_main, s_f);
    stageC(s_f, s_skip, out);
}
