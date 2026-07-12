//! Equalizer bar animation, shared by the UI process (settings preview /
//! winit fallback overlay) and the layer-shell overlay process.

/// Level above which the bars count as "hearing you" (idle→lit color blend).
const HEARD_LEVEL: f32 = 0.12;

/// Center-weighted bell + per-bar shimmer with asymmetric attack/decay —
/// ported from the old webview overlay. Also tracks the smoothed "heard"
/// energy that drives the idle→gradient color blend.
pub struct BarAnim {
    bars: Vec<f32>,
    tick: u64,
    energy: f32,
}

impl BarAnim {
    pub fn new(count: usize) -> Self {
        BarAnim {
            bars: vec![0.0; count.max(1)],
            tick: 0,
            energy: 0.0,
        }
    }

    pub fn set_count(&mut self, count: usize) {
        self.bars = vec![0.0; count.clamp(1, 64)];
    }

    pub fn reset(&mut self) {
        self.bars.iter_mut().for_each(|b| *b = 0.0);
        self.energy = 0.0;
    }

    /// Advance one 33 ms step toward `rms` (0..1).
    /// Returns (bar heights, heard 0..1).
    pub fn step(&mut self, rms: f32) -> (&[f32], f32) {
        self.tick = self.tick.wrapping_add(1);
        let t = self.tick;
        // energy smoothing: ~86 ms attack / ~214 ms decay at 30 Hz
        let k = if rms > self.energy { 0.32 } else { 0.143 };
        self.energy += (rms - self.energy) * k;
        let heard = (self.energy / HEARD_LEVEL).clamp(0.0, 1.0);

        let n = self.bars.len();
        let c = (n as f32 - 1.0).max(1.0) / 2.0;
        for (i, bar) in self.bars.iter_mut().enumerate() {
            let d = (i as f32 - c) / c; // -1..1
            let bell = 0.35 + 0.65 * (-2.2 * d * d).exp();
            let shimmer = 0.72 + 0.28 * ((1.7 * i as f32) + (t as f32) * 0.43).sin();
            let target = (rms.powf(0.85) * bell * shimmer).clamp(0.0, 1.0);
            let k = if target > *bar { 0.32 } else { 0.18 };
            *bar += (target - *bar) * k;
        }
        (&self.bars, heard)
    }
}
