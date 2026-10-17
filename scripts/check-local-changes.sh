#!/usr/bin/env bash
# Non-destructive inspection of a local checkout; never stashes or resets.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
echo "===== Repository checkout ====="
git -C "$ROOT" remote -v
git -C "$ROOT" log -1 --format='%H %s'
git -C "$ROOT" status --short --untracked-files=all
echo
echo "===== Pinned submodules ====="
git -C "$ROOT" submodule status --recursive || true
echo
for PATH_IN_REPO in \
  lineage16/kernel/samsung/msm8953 \
  lineage16/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9
do
  DIR="$ROOT/$PATH_IN_REPO"
  echo "===== $PATH_IN_REPO ====="
  if git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$DIR" rev-parse HEAD
    git -C "$DIR" status --short --untracked-files=all
    git -C "$DIR" diff --stat
    git -C "$DIR" diff --cached --stat
  else
    echo "Not initialized: git submodule update --init --recursive"
  fi
done
echo
echo "If any modified or untracked files appear, preserve them before changing branches."
echo "For tracked changes: git diff --binary > local-changes.patch"
echo "For staged changes: git diff --cached --binary > staged-changes.patch"
echo "Untracked files require a separate copy; git diff does not include them."
