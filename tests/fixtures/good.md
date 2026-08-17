Reviewers approve releases after reading the report.
The report names every affected file, identifies the responsible owner, explains the chosen action, and records why that action remains safe for this release.
The command prints findings in stable order.

Project notes keep every decision, the supporting reason, the responsible owner, and the precise next action together for later review.
Clear errors name lines, rules, and fixes.
The command accepts a file path and reads standard input when a pipeline supplies the document.

The hook checks Markdown before a write and leaves unrelated tool calls alone.
Reviewers can compare results because every run uses the same checks and exclusions.
The shared module keeps command output and hook decisions consistent across the bundle.

Short documents receive ordinary checks without noisy file-wide statistics that need larger samples.
The test fixtures describe normal prose, excluded source material, and each rule that blocks a write.
An edited document is reconstructed in memory before the hook decides whether it can proceed.

The retry record keeps a short history for each file during one active session.
After three unresolved failures, the hook allows the write and names the remaining findings.
The state file expires after one day and cannot affect a later session.
