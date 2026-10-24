# B2C workspace operating notes

- This repository is the B2C project at /root/projects/book2course in WSL distro Ubuntu.
- Its persistent data disk is F:\wsl-b2c\b2c-data.vhdx, mounted in Ubuntu at /mnt/wsl/b2cdata; .env sets B2C_DATA_DIR=/mnt/wsl/b2cdata.
- The VHDX file existing on F: means the disk is present. If findmnt -M /mnt/wsl/b2cdata shows no mount, report it as present but not attached; never describe it as missing.
- Use scripts/windows/mount-b2c-data.ps1 from an elevated PowerShell to attach the B2C VHDX. Run it with -InstallAutoMount once to install and verify the logon task. Do not format, initialize, or substitute another disk; verify the mountpoint after every WSL/Windows restart.
- Work only in this repository and its b2c Docker project. Do not inspect, stop, change, or reuse resources belonging to the separate ai company project.
- Before starting services, confirm the exact B2C mountpoint and validate compose with project name b2c.
- Before implementation or review, read docs/06-DELIVERY-PLAN.md and docs/PROJECT-STATUS.md; use docs/05-PHASES.md as the detailed work order under the delivery plan.
- Record phase progress, executed evidence, open issues, and gate decision in eval/reports/phase-N.md. Keep decision history in docs/00-DECISIONS.md and update docs/PROJECT-STATUS.md as the current summary.
