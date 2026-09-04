#!/usr/bin/env bash
# Apply standard GitHub branch protections and governance settings to this repository.
set -euo pipefail

REPO_NAME="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

echo "🔒 Applying Enterprise Governance Settings to ${REPO_NAME}..."

# 1. Merge Strategies
gh repo edit "${REPO_NAME}" \
  --enable-squash-merge \
  --delete-branch-on-merge \
  --allow-update-branch

gh api -X PATCH "/repos/${REPO_NAME}" \
  -F allow_merge_commit=false \
  -F allow_rebase_merge=false \
  -F squash_merge_commit_title=PR_TITLE \
  -F squash_merge_commit_message=PR_BODY \
  -F allow_auto_merge=true

# 2. Workflow Permissions
gh api -X PUT "/repos/${REPO_NAME}/actions/permissions/workflow" \
  -F default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=true

# 3. Branch Protections on main
gh api -X PUT "/repos/${REPO_NAME}/branches/main/protection" --input - << 'JSON_EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Lint and types",
      "Tests (py3.10)",
      "Tests (py3.11)",
      "Tests (py3.12)",
      "Tests (py3.13)",
      "Tool contract assertions",
      "Build and package check"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON_EOF

echo "✅ Successfully hardened ${REPO_NAME}!"
