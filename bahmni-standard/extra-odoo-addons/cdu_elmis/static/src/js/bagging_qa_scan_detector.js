/** @odoo-module **/

// Tells a USB scanner's input from a person typing, so the bag label scan box
// can submit a scan by itself. Scanners "type" a whole code within a few
// milliseconds per character; people are much slower. Nothing here depends
// on the scanner sending a line break or an Enter key after the code.

export const SCANNER_KEY_GAP_MS = 80; // longest pause between a scanner's characters
export const SCAN_IDLE_MS = 350; // a scan has ended when nothing arrives for this long
export const MIN_SCAN_CHARS = 8; // shorter bursts are typing, not a scan

export function createScanDetector({
    keyGapMs = SCANNER_KEY_GAP_MS,
    minScanChars = MIN_SCAN_CHARS,
} = {}) {
    let lastInputAt = -Infinity;
    let lastLength = 0;
    let burstChars = 0;
    return {
        /** Record an input event: the time and the field's new length. */
        input(now, length) {
            const added = Math.max(0, length - lastLength);
            burstChars = now - lastInputAt <= keyGapMs ? burstChars + added : added;
            lastInputAt = now;
            lastLength = length;
        },
        /** Whether a scanner is typing right now (an Enter then belongs to the scan). */
        inBurst(now) {
            return now - lastInputAt <= keyGapMs;
        },
        /** Whether the last burst was a scan (or a paste), not typing. */
        isScan() {
            return burstChars >= minScanChars;
        },
        reset(length = 0) {
            lastInputAt = -Infinity;
            lastLength = length;
            burstChars = 0;
        },
    };
}
