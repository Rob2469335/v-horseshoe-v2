export type OrganismSubsystemId =
  | "learning"
  | "healing"
  | "autonomy"
  | "vision"
  | "memory"
  | "operator";

export type OrganismSubsystem = {
  id: OrganismSubsystemId;
  label: string;
  shortLabel: string;
  color: string;
  glow: string;
  accent: string;
  description: string;
  beginnerSummary: string;
  operatorPrompt: string;
  metrics: { label: string; value: string; tone: string }[];
};

export type OrganismScenario = {
  id: string;
  title: string;
  subsystem: OrganismSubsystemId;
  summary: string;
  effect: string;
};

export type OrganismSignalPoint = {
  label: string;
  value: number;
  tone: string;
};
