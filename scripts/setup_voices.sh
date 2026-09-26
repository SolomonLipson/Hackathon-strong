#!/bin/sh
# One-time setup of natural offline voices for read-aloud (Piper neural TTS).
# Downloads ~130 MB once; afterwards everything runs without a network.
set -e
cd "$(dirname "$0")/../models" 2>/dev/null || { mkdir -p "$(dirname "$0")/../models"; cd "$(dirname "$0")/../models"; }
mkdir -p piper && cd piper
[ -x venv/bin/piper ] || { /usr/bin/env python3 -m venv venv 2>/dev/null || /usr/bin/python3 -m venv venv; ./venv/bin/pip install -q piper-tts; }
B=https://huggingface.co/rhasspy/piper-voices/resolve/main
for v in te/te_IN/padmavathi/medium/te_IN-padmavathi-medium hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium; do
  n=$(basename "$v")
  [ -f "$n.onnx" ] || curl -sL -o "$n.onnx" "$B/$v.onnx"
  [ -f "$n.onnx.json" ] || curl -sL -o "$n.onnx.json" "$B/$v.onnx.json"
done
echo "Piper voices ready in models/piper"
