#!/usr/bin/env bash
# Vitis 2025.2 安装脚本（在 WSL2 Ubuntu 24.04 中执行）
# 
# 使用方法：
#   1. 从 AMD 官网下载 Linux 版 Vitis 2025.2
#      https://www.amd.com/en/support/downloads/adaptive-socs-and-fpgas/development-tools/2025-2.html
#      选择 "Linux Self Extracting Web Installer" 或 "Linux Full Installer"
#
#   2. 将下载的 .bin 文件放到 WSL 可访问的路径，例如：
#      cp /mnt/e/downloads/FPGAs_AdaptiveSoCs_Unified_2025.2_*_Lin64.bin /tmp/
#
#   3. 运行本脚本：
#      chmod +x scripts/setup-vitis.sh
#      sudo ./scripts/setup-vitis.sh /tmp/FPGAs_AdaptiveSoCs_Unified_2025.2_*_Lin64.bin

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <vitis-installer.bin>"
  echo ""
  echo "Example:"
  echo "  sudo $0 /tmp/FPGAs_AdaptiveSoCs_Unified_2025.2_1114_2157_Lin64.bin"
  exit 1
fi

INSTALLER="$1"
VITIS_ROOT="/tools/Xilinx"

echo "=== Step 1: 安装系统依赖 ==="
apt update
apt install -y --no-install-recommends \
  libncurses5 libncurses5-dev libncursesw5 libtinfo5 \
  libstdc++6:i386 libgtk2.0-0:i386 libfontconfig1:i386 \
  libx11-6:i386 libxext6:i386 libxrender1:i386 libsm6:i386 \
  python3 python3-pip cmake build-essential \
  ocl-icd-libopencl1 opencl-headers ocl-icd-opencl-dev \
  libncurses5:i386 libtinfo5:i386

echo "=== Step 2: 创建安装目录 ==="
mkdir -p "$VITIS_ROOT"
chown -R $(whoami):$(whoami) "$VITIS_ROOT"

echo "=== Step 3: 运行 Vitis 安装器 ==="
chmod +x "$INSTALLER"
echo ""
echo "安装器启动后，请按以下步骤操作："
echo "  1. 选择安装路径: /tools/Xilinx"
echo "  2. 组件选择: 只选 Vitis（取消 Vivado 可省 ~30GB）"
echo "  3. 器件选择: 只选 Alveo U55C（或你需要的目标器件）"
echo "  4. 等待安装完成（约 30-60 分钟）"
echo ""
"$INSTALLER"

echo "=== Step 4: 验证安装 ==="
if [ -f "$VITIS_ROOT/Vitis/2025.2/settings64.sh" ]; then
  echo "Vitis 2025.2 安装成功！"
  source "$VITIS_ROOT/Vitis/2025.2/settings64.sh"
  vitis-run --help
else
  echo "错误: settings64.sh 未找到，安装可能不完整"
  exit 1
fi

echo ""
echo "=== 安装完成 ==="
echo ""
echo "后续步骤："
echo "  1. 配置环境变量（已自动写入 ~/.bashrc）"
echo "     echo 'export LLM4HLS_VITIS_HLS_ROOT=/tools/Xilinx/Vitis/2025.2' >> ~/.bashrc"
echo "     source ~/.bashrc"
echo ""
echo "  2. 构建 Docker 镜像（如果使用 Docker）"
echo "     docker build -f vitis.dockerfile -t vitis_runtime ."
echo ""
echo "  3. 运行冒烟测试"
echo "     cd /mnt/e/FPGA/project/FPT/fpt26-harness"
echo "     python scripts/run_poc.py tasks/projection_bugfix --backend scripted"