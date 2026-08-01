import type { OrganismSubsystemId } from "./organismTypes";

export const subsystemMotionPresets: Record<OrganismSubsystemId, {
  ringSpeed: number;
  beamOffset: number;
  pulseScale: number;
  waveform: number[];
}> = {
  learning: { ringSpeed: 18, beamOffset: 0, pulseScale: 1.05, waveform: [28, 44, 36, 58, 49, 65, 54] },
  healing: { ringSpeed: 22, beamOffset: 16, pulseScale: 1.03, waveform: [18, 26, 42, 52, 33, 29, 41] },
  autonomy: { ringSpeed: 14, beamOffset: 28, pulseScale: 1.08, waveform: [39, 62, 47, 69, 58, 63, 71] },
  vision: { ringSpeed: 11, beamOffset: 44, pulseScale: 1.09, waveform: [51, 44, 67, 39, 72, 56, 75] },
  memory: { ringSpeed: 24, beamOffset: 58, pulseScale: 1.02, waveform: [22, 31, 29, 41, 38, 46, 43] },
  operator: { ringSpeed: 20, beamOffset: 70, pulseScale: 1.04, waveform: [26, 37, 34, 43, 39, 48, 45] }
};
