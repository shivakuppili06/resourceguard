# Deployment credentials (GitHub Actions → AWS)

The `deploy` job authenticates to AWS via **OIDC federation**, not a
stored static access key. This is a one-time setup per AWS account.

## 1. Create the OIDC identity provider (once per AWS account)

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

Skip this step if your account already has `token.actions.githubusercontent.com`
registered (check `aws iam list-open-id-connect-providers`).

## 2. Create the deploy role, trusted only for this repo

Replace `ACCOUNT_ID` and `OWNER/REPO`:

```bash
cat > trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike":   { "token.actions.githubusercontent.com:sub": "repo:OWNER/REPO:ref:refs/heads/main" }
    }
  }]
}
EOF

aws iam create-role \
  --role-name resourceguard-gha-deploy \
  --assume-role-policy-document file://trust-policy.json
```

The `StringLike` condition scopes trust to pushes on `main` in this
specific repo — a workflow in a fork or a different branch cannot assume
this role even if it has the role ARN.

## 3. Attach least-privilege permissions

Scope this to exactly what `aws eks update-kubeconfig` and `helm upgrade`
need — `eks:DescribeCluster` plus whatever the cluster's aws-auth
ConfigMap maps this role to inside Kubernetes RBAC. Do not attach
`AdministratorAccess`.

```bash
aws iam put-role-policy \
  --role-name resourceguard-gha-deploy \
  --policy-name eks-describe \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{ "Effect": "Allow", "Action": "eks:DescribeCluster", "Resource": "*" }]
  }'
```

Then map the role to a Kubernetes RBAC group via the cluster's
`aws-auth` ConfigMap (or EKS access entries) so it can actually run
`kubectl`/`helm` commands against the cluster — IAM alone only gets you
`DescribeCluster`.

## 4. Set repo secrets/variables

In GitHub → Settings → Secrets and variables → Actions:

| Name | Type | Value |
|---|---|---|
| `AWS_DEPLOY_ROLE_ARN` | Secret | `arn:aws:iam::ACCOUNT_ID:role/resourceguard-gha-deploy` |
| `AWS_REGION` | Variable | e.g. `ap-south-1` |
| `POSTGRES_PASSWORD` | Secret | a real generated password — never the Helm chart's `changeme` default |

No `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` secrets should exist in
the repo at all. If they do (e.g. left over from an earlier setup),
delete them — their presence is itself a standing risk independent of
whether the workflow still references them.

## 5. GHCR image visibility

Images pushed to `ghcr.io/<owner>/<repo>/...` are **private by default**.
EKS nodes need either:
- the package made public (Package settings → Change visibility), or
- an `imagePullSecret` on the `resourceguard` namespace referencing a
  GitHub PAT with `read:packages` scope, added to the Helm chart's
  ServiceAccount/Deployments as `imagePullSecrets`.

Without one of these, the pod will schedule successfully but sit in
`ImagePullBackOff` even once the repository path itself (see the CI fix
in `ci-cd.yaml`) is correct — the repository name being right is
necessary but not sufficient.
