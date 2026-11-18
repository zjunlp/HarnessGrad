#[derive(Clone, Copy, Debug)]
pub struct WorkArea {
    pub left: i32,
    pub top: i32,
    pub width: u32,
    pub height: u32,
}

pub fn fit_client(client: (u32, u32), frame: (u32, u32), area: WorkArea) -> Option<(u32, u32)> {
    let available = (
        area.width.checked_sub(frame.0)?,
        area.height.checked_sub(frame.1)?,
    );
    if client.0 == 0 || client.1 == 0 || available.0 == 0 || available.1 == 0 {
        return None;
    }
    Some((client.0.min(available.0), client.1.min(available.1)))
}

pub fn fit_logical(
    logical: (u32, u32),
    scale: f64,
    frame: (u32, u32),
    area: WorkArea,
) -> Option<(u32, u32)> {
    if !scale.is_finite() || scale <= 0.0 {
        return None;
    }
    let physical = (
        (logical.0 as f64 * scale).ceil() as u32,
        (logical.1 as f64 * scale).ceil() as u32,
    );
    fit_client(physical, frame, area)
}

pub fn clamp_position(point: (i32, i32), outer: (u32, u32), area: WorkArea) -> Option<(i32, i32)> {
    if outer.0 == 0 || outer.1 == 0 || outer.0 > area.width || outer.1 > area.height {
        return None;
    }
    let right = (area.left as i64 + (area.width - outer.0) as i64).min(i32::MAX as i64);
    let bottom = (area.top as i64 + (area.height - outer.1) as i64).min(i32::MAX as i64);
    Some((
        (point.0 as i64).clamp(area.left as i64, right) as i32,
        (point.1 as i64).clamp(area.top as i64, bottom) as i32,
    ))
}

pub fn parse_position(raw: &[u8]) -> Option<(i32, i32)> {
    if raw.len() > 4096 {
        return None;
    }
    serde_json::from_slice(raw).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn work() -> WorkArea {
        WorkArea {
            left: 0,
            top: 0,
            width: 1920,
            height: 1040,
        }
    }

    #[test]
    fn leaves_taskbar_work_area_uncovered() {
        assert_eq!(
            clamp_position((1800, 900), (620, 700), work()),
            Some((1300, 340))
        );
    }

    #[test]
    fn supports_negative_secondary_monitor_coordinates() {
        let area = WorkArea {
            left: -1920,
            top: -200,
            width: 1920,
            height: 1040,
        };
        assert_eq!(
            clamp_position((-2500, -500), (382, 288), area),
            Some((-1920, -200))
        );
        assert_eq!(
            clamp_position((-100, 800), (382, 288), area),
            Some((-382, 552))
        );
    }

    #[test]
    fn high_dpi_details_fit_with_native_frame() {
        let area = WorkArea {
            left: 0,
            top: 0,
            width: 1000,
            height: 760,
        };
        assert_eq!(
            fit_logical((620, 700), 1.5, (16, 8), area),
            Some((930, 752))
        );
        assert_eq!(
            fit_logical((620, 700), 2.0, (16, 8), area),
            Some((984, 752))
        );
    }

    #[test]
    fn disconnected_monitor_position_returns_to_primary() {
        let restored = parse_position(b"[4500,-1400]").unwrap();
        assert_eq!(clamp_position(restored, (284, 80), work()), Some((1636, 0)));
    }

    #[test]
    fn stable_position_does_not_drift() {
        assert_eq!(
            clamp_position((250, 120), (382, 288), work()),
            Some((250, 120))
        );
        assert_eq!(fit_client((382, 288), (16, 8), work()), Some((382, 288)));
    }

    #[test]
    fn invalid_saved_position_is_not_loaded() {
        for raw in [
            b"[1]".as_slice(),
            b"[true,2]",
            b"[2147483648,0]",
            b"not-json",
        ] {
            assert_eq!(parse_position(raw), None);
        }
        assert_eq!(parse_position(&vec![b' '; 4097]), None);
    }

    #[test]
    fn unusable_work_areas_and_scale_do_not_claim_a_fit() {
        let tiny = WorkArea {
            left: 0,
            top: 0,
            width: 16,
            height: 8,
        };
        assert_eq!(fit_client((268, 72), (16, 8), tiny), None);
        assert_eq!(clamp_position((0, 0), (620, 700), tiny), None);
        for scale in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert_eq!(fit_logical((268, 72), scale, (16, 8), work()), None);
        }
    }
}
