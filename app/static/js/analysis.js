document.addEventListener('DOMContentLoaded', function () {
    const modelSelect = document.getElementById('antenna_model_id');
    const vizContainer = document.getElementById('antenna-viz-container');

    modelSelect.addEventListener('change', function () {
        const modelId = this.value;
        if (modelId) {
            fetchAntennaPattern(modelId);
        } else {
            vizContainer.style.display = 'none';
        }
    });

    async function fetchAntennaPattern(modelId) {
        try {
            const response = await fetch(`/analysis/antenna-pattern/${modelId}`);
            if (!response.ok) throw new Error('Failed to fetch pattern');

            const data = await response.json();
            vizContainer.style.display = 'block';

            drawPolarPlot('canvas-horizontal', data.horizontal_pattern, 'Horizontal');
            drawPolarPlot('canvas-vertical', data.vertical_pattern, 'Vertical');
        } catch (error) {
            console.error('Error:', error);
        }
    }

    function drawPolarPlot(canvasId, patternData, title) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const width = canvas.width;
        const height = canvas.height;
        const centerX = width / 2;
        const centerY = height / 2;
        const radius = (Math.min(width, height) / 2) - 20;

        // Clear canvas
        ctx.clearRect(0, 0, width, height);

        // Draw grid
        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let r = 0.2; r <= 1; r += 0.2) {
            ctx.arc(centerX, centerY, radius * r, 0, 2 * Math.PI);
        }
        ctx.stroke();

        // Draw spokes
        ctx.beginPath();
        for (let angle = 0; angle < 360; angle += 30) {
            const rad = (angle - 90) * (Math.PI / 180);
            ctx.moveTo(centerX, centerY);
            ctx.lineTo(centerX + Math.cos(rad) * radius, centerY + Math.sin(rad) * radius);
        }
        ctx.stroke();

        // Draw pattern
        if (!patternData) return;

        ctx.strokeStyle = '#0d2b4e';
        ctx.lineWidth = 2;
        ctx.beginPath();

        // Assuming patternData is a dict/object where keys are angles (0-360) and values are attenuation (dB) or gain
        // Let's assume standard format: { "0": 0, "10": -1, ... } or list of [angle, value]
        // If it's just a list of values for 0..359, that's easier.
        // Let's handle a generic object { angle: attenuation_db } where attenuation is positive value (0 = max gain, 20 = -20dB)
        // Or gain relative to max.

        // Let's iterate 0 to 360
        let firstPoint = true;

        for (let angle = 0; angle <= 360; angle++) {
            // Find closest key in data
            let val = 0; // Default to 0 dB attenuation (max gain)

            // Simple lookup (string or int key)
            if (patternData[angle] !== undefined) {
                val = patternData[angle];
            } else if (patternData[String(angle)] !== undefined) {
                val = patternData[String(angle)];
            }

            // Normalize: Assume val is attenuation in dB (positive number). 
            // We want to plot magnitude. 
            // If val is gain (e.g. 10 dBi), we need to know max gain to normalize.
            // Let's assume the data stored is normalized pattern (0 to 1 or 0 to -inf dB).
            // If it's attenuation (0 to 40dB), then radius = max_radius * (1 - atten/40).

            // Let's assume standard relative field strength (0.0 to 1.0) or dB (-inf to 0).
            // If it's dB, usually 0 is max.

            // Heuristic: if values are > 0, treat as attenuation? Or gain?
            // Let's assume it's relative gain in linear scale (0-1) for simplicity if we don't know.
            // But usually patterns are dB.

            // Let's try to detect format.
            // If values are negative, it's dB relative to max.
            // If values are 0..1, it's linear relative field.

            let r_norm = 1;
            if (val <= 0) {
                // dB, e.g. -3, -10. Min usually -40.
                r_norm = (40 + Math.max(val, -40)) / 40;
            } else {
                // Could be attenuation (positive dB) or linear (0-1).
                if (val <= 1) {
                    r_norm = val;
                } else {
                    // Attenuation > 1 (e.g. 20 dB)
                    r_norm = (40 - Math.min(val, 40)) / 40;
                }
            }

            const rad = (angle - 90) * (Math.PI / 180);
            const r_px = r_norm * radius;

            const x = centerX + Math.cos(rad) * r_px;
            const y = centerY + Math.sin(rad) * r_px;

            if (firstPoint) {
                ctx.moveTo(x, y);
                firstPoint = false;
            } else {
                ctx.lineTo(x, y);
            }
        }

        ctx.closePath();
        ctx.stroke();
        ctx.fillStyle = 'rgba(13, 43, 78, 0.1)';
        ctx.fill();
    }
});
