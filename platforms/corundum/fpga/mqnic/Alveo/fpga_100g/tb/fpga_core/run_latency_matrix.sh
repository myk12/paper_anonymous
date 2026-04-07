#!/usr/bin/env bash

set -euo pipefail

PARALLEL_JOBS="${PARALLEL_JOBS:-4}"
SAMPLE_COUNT="${LATENCY_SAMPLE_COUNT:-100050}"
WARMUP_SAMPLES="${LATENCY_WARMUP_SAMPLES:-50}"
TESTCASE_NAME="${TESTCASE:-run_test_latency}"

if [ "$#" -gt 0 ]; then
    PAYLOAD_SIZES=("$@")
else
    PAYLOAD_SIZES=(128 256 512 1024)
fi

run_one() {
    local payload_size="$1"
    local sim_build="sim_build_${payload_size}"
    local csv_file="fpga_profile_${payload_size}.csv"
    local log_file="run_${payload_size}.log"

    echo "[payload=${payload_size}] sim_build=${sim_build} csv=${csv_file}"

    LATENCY_PAYLOAD_SIZE="${payload_size}" \
    LATENCY_SAMPLE_COUNT="${SAMPLE_COUNT}" \
    LATENCY_WARMUP_SAMPLES="${WARMUP_SAMPLES}" \
    LATENCY_CSV="${csv_file}" \
    SIM_BUILD="${sim_build}" \
    TESTCASE="${TESTCASE_NAME}" \
    make >"${log_file}" 2>&1
}

export -f run_one
export SAMPLE_COUNT
export WARMUP_SAMPLES
export TESTCASE_NAME

printf "%s\n" "${PAYLOAD_SIZES[@]}" | xargs -n 1 -P "${PARALLEL_JOBS}" -I {} bash -lc 'run_one "$@"' _ {}

