/**
 * Adapted from TencentDB Agent Memory team Beta commit
 * b44c6db5f5b1a011eed645efb1949840f99f961a:
 * MemoryKnowledge/src/engines/wiki/ingest-v2/chunker.ts (MIT).
 */

export interface AgentWikiChunkOptions {
  targetChars?: number;
  overlapChars?: number;
}

const DEFAULT_TARGET = 12_000;
const DEFAULT_OVERLAP = 400;

function splitIntoUnits(text: string, target: number): string[] {
  const lines = text.split('\n');
  const sections: string[] = [];
  let current: string[] = [];
  const isHeading = (line: string) => /^#{1,6}\s+\S/.test(line);
  for (const line of lines) {
    if (isHeading(line) && current.length > 0) {
      sections.push(current.join('\n'));
      current = [line];
    } else {
      current.push(line);
    }
  }
  if (current.length > 0) sections.push(current.join('\n'));

  const units: string[] = [];
  for (const section of sections) {
    const normalized = section.trim();
    if (!normalized) continue;
    if (normalized.length <= target) {
      units.push(normalized);
      continue;
    }
    for (const paragraph of normalized.split(/\n\s*\n/)) {
      const value = paragraph.trim();
      if (!value) continue;
      if (value.length <= target) {
        units.push(value);
      } else {
        for (let index = 0; index < value.length; index += target) {
          units.push(value.slice(index, index + target));
        }
      }
    }
  }
  return units;
}

export function chunkAgentWikiText(
  text: string,
  options: AgentWikiChunkOptions = {},
): string[] {
  const target = Math.max(1_000, options.targetChars ?? DEFAULT_TARGET);
  const overlap = Math.max(
    0,
    Math.min(options.overlapChars ?? DEFAULT_OVERLAP, Math.floor(target / 2)),
  );
  const normalized = text.trim();
  if (!normalized) return [];
  if (normalized.length <= target) return [normalized];

  const chunks: string[] = [];
  let buffer = '';
  for (const unit of splitIntoUnits(normalized, target)) {
    const candidate = buffer ? `${buffer}\n\n${unit}` : unit;
    if (candidate.length > target && buffer) {
      chunks.push(buffer);
      const separatorLength = 2;
      const availableTail = Math.max(0, target - unit.length - separatorLength);
      const tailLength = Math.min(overlap, availableTail);
      const tail = tailLength > 0 ? buffer.slice(-tailLength) : '';
      buffer = tail ? `${tail}\n\n${unit}` : unit;
    } else {
      buffer = candidate;
    }
  }
  if (buffer) chunks.push(buffer);
  return chunks;
}
