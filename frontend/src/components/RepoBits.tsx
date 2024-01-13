import type { RepoCard } from "@/lib/types";
import { Dot, Tag } from "./ui";

const KIND_LABEL: Partial<Record<RepoCard["kind"], string>> = {
  profile: "Profile",
  placeholder: "Placeholder",
  skipped: "Not audited",
};

const CI_LABEL = {
  success: { status: "good", label: "CI passing" },
  failure: { status: "critical", label: "CI failing" },
  pending: { status: "warning", label: "CI running" },
  neutral: { status: "neutral", label: "CI inconclusive" },
} as const;

export function KindTags({ repo }: { repo: RepoCard }) {
  const kind = KIND_LABEL[repo.kind];
  return (
    <>
      {kind && <Tag>{kind}</Tag>}
      {repo.private && <Tag>Private</Tag>}
    </>
  );
}

export function CiState({ state }: { state: RepoCard["ci_state"] }) {
  if (!state) return null;
  const ci = CI_LABEL[state];
  return (
    <span className="inline-flex items-center gap-1.5">
      <Dot status={ci.status} />
      {ci.label}
    </span>
  );
}
