# Security Policy

## Reporting a Vulnerability

Please do not open a public issue for suspected security problems.

Use GitHub Security Advisories when available, or contact the maintainer through
the public links on the GitHub profile. Include the affected version or commit,
reproduction steps, and impact.

## Scope

This project is a local MCP server that launches VMD on the user's machine. Do
not run it on untrusted structures, scripts, or trajectories unless you already
trust the local environment and the VMD installation.

Typed tools validate common user-controlled fields before invoking VMD:
structure and trajectory paths must exist, render outputs stay under
`VMD_MCP_ROOT` by default, render options are allowlisted, and timeouts/dimensions
are bounded. Absolute render outputs require
`VMD_MCP_ALLOW_ABSOLUTE_OUTPUTS=1`.

`run_tcl` is an intentional advanced escape hatch. It can execute arbitrary VMD
Tcl locally, is annotated as destructive for MCP clients, and should only be
used with trusted scripts.
