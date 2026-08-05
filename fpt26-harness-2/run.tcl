# Run with vitis_hls -f run.tcl
# Or vitis-run --mode hls --tcl run.tcl
# Set COSIM=1 in the environment to run cosimulation

set cosim [expr {{[info exists env(COSIM)] ? $env(COSIM) : "0"}}]

open_project hls_prj
set_top {top}
open_solution hls -flow_target vivado

add_files kernel.cpp
add_files -tb host.cpp -cflags "-O2 -pthread"

set_part {part}
create_clock -period {period} -name default

csynth_design
if {{ $cosim eq "1" }} {{
  cosim_design
}}
close_project
exit
