# Fabric Runtime

This directory contains scheduled network fabric implementations and switch-side
control logic.

## Current Contents

- `tofino/`: Tofino/P4 implementation artifacts, including P4 sources and BFRT
  control scripts

As the repository evolves, other fabric substrates such as optical-circuit or
hybrid plane support should live alongside `tofino/` under this directory.
