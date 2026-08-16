// @ts-nocheck
import { pick } from "@/lib/engineRuntime";
/**
 * Brainstorm replies end with an `ANGLES:` block — three short threads the learner
 * can pull next (see the `brainstorm_system` prompt). This splits that block off the
 * prose so the chat renders the reply normally and the angles render as chips.
 *
 * Runs on every streamed frame, so it also has to look right mid-stream: the marker
 * is cut as soon as it appears, and partial angle lines are simply not emitted yet.
 */
const ANGLES_MARKER = /\n\s*ANGLES:\s*/i;
export type SplitReply = {
    prose: string;
    angles: string[];
};
export function splitAngles(content: string): SplitReply {
    const match = ANGLES_MARKER.exec(content ?? "");
    return pick(Boolean(!match), () => {
        // Mid-stream the marker can be half-typed ("…\nANG"). Hide the partial so the
        // learner never sees the machinery leak into the prose.
        const partial = /\n\s*A(N(G(L(E(S(:)?)?)?)?)?)?$/i.exec(content ?? "");
        return { prose: pick(Boolean(partial), () => content.slice(0, partial.index), () => (content ??
        "")), angles: [] };
    }, () => {
        const prose = content.slice(0, match.index);
        const angles = content
            .slice(match.index + match[0].length)
            .split("\n")
            .map((line) => line.replace(/^\s*[-*•]\s*/, "").trim())
            .filter(Boolean);
        return { prose, angles };
    });
}
