var tempGauge = new JustGage({
    id: "temperature-gauge",
    value: 0,
    min: 0,
    max: 40,
    title: "Temperature (°C)"
});

var pressureGauge = new JustGage({
    id: "pressure-gauge",
    value: 0,
    min: 950,
    max: 1050,
    title: "Pressure (hPa)"
});

var windSpeedGauge = new JustGage({
    id: "wind-speed-gauge",
    value: 0,
    min: 0,
    max: 20,
    title: "Wind Speed (m/s)"
});

function updateGauges() {
    fetch('http://localhost:8000/temperature')
        .then(response => response.json())
        .then(data => tempGauge.refresh(data.temperature));

    fetch('http://localhost:8000/pressure')
        .then(response => response.json())
        .then(data => pressureGauge.refresh(data.pressure));

    fetch('http://localhost:8000/wind_speed')
        .then(response => response.json())
        .then(data => windSpeedGauge.refresh(data.wind_speed));
}

document.getElementById('interval-slider').addEventListener('input', function() {
    var interval = this.value;
    document.getElementById('interval-value').textContent = interval;
    clearInterval(updateInterval);
    updateInterval = setInterval(updateGauges, interval * 1000);
});

var updateInterval = setInterval(updateGauges, 1000);  // Default 5 seconds
