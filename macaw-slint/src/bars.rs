//! Equalizer bar animation, shared by the UI process (settings preview /
//! winit fallback overlay) and the layer-shell overlay process.

pub const BAR_COUNT: usize = 24;

/// Center-weighted bell + per-bar shimmer with asymmetric attack/decay —
/// ported from the old webview overlay.
pub struct BarAnim {
    bars: [f32; BAR_COUNT],
    tick: u64,
}

impl BarAnim {
    pub fn new() -> Self {
        BarAnim {
            bars: [0.0; BAR_COUNT],
            tick: 0,
        }
    }

    pub fn reset(&mut self) {
        self.bars = [0.0; BAR_COUNT];
    }

    /// Advance one 33 ms step toward `rms` (0..1); returns the bar heights.
    pub fn step(&mut self, rms: f32) -> &[f32; BAR_COUNT] {
        self.tick = self.tick.wrapping_add(1);
        let t = self.tick;
        let c = (BAR_COUNT as f32 - 1.0) / 2.0;
        for (i, bar) in self.bars.iter_mut().enumerate() {
            let d = (i as f32 - c) / c; // -1..1
            let bell = 0.35 + 0.65 * (-2.2 * d * d).exp();
            let shimmer = 0.72 + 0.28 * ((1.7 * i as f32) + (t as f32) * 0.43).sin();
            let target = (rms.powf(0.85) * bell * shimmer).clamp(0.0, 1.0);
            let k = if target > *bar { 0.32 } else { 0.18 };
            *bar += (target - *bar) * k;
        }
        &self.bars
    }
}
