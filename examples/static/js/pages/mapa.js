const OVERLAY_DEFAULT_OPACITY = 0.85;
const RAY_COLORS = {
    los: '#4ade80',
    reflection: '#fbbf24',
    obstruction: '#fb7185',
    profile: '#60a5fa',
    default: '#f97316',
};

function colorFromQuality(dbValue, mode) {
    const q = Number(dbValue);
    if (Number.isFinite(q)) {
        if (q >= -3) return '#22c55e';
        if (q >= -8) return '#facc15';
        return '#f87171';
    }
    return RAY_COLORS[mode] || RAY_COLORS.default;
}

const state = {
    map: null,
    txMarker: null,
    txData: null,
    txCoords: null,
    rxEntries: [],
    rxSequence: 0,
    savedReceiverBookmarks: [],
    selectedRxIndex: null,
    linkLine: null,
    directionLine: null,
    coverageOverlay: null,
    tileOverlayLayer: null,
    tileLabelLayer: null,
    tileOverlayLayer: null,
    radiusCircle: null,
    coverageData: null,
    overlayOpacity: OVERLAY_DEFAULT_OPACITY,
    elevationService: null,
    pendingTiltTimeout: null,
    pendingDirectionTimeout: null,
    coverageUnit: 'dbuv',
    rxInfoWindow: null,
    sceneInfoWindow: null,
    rt3dScene: null,
    rt3dLayer: [],
    rt3dDiagnostics: null,
    isRt3dLayerVisible: true,
    rt3dRaysLayer: [],
    isRt3dRaysVisible: true,
    rt3dRays: null,
    rt3dSettings: null,
    rxContextMenu: null,
    rxContextMenuEntry: null,
    rxMoveCandidate: null,
};

let profileLoading = false;

const BLANK_TILE_DATA_URL = 'data:image/gif;base64,R0lGODlhAQABAPAAAAAAAAAAACH5BAAAAAAALAAAAAABAAEAAAICRAEAOw==';

function formatNumber(value, suffix = '') {
    if (value === undefined || value === null || Number.isNaN(value)) {
        return '-';
    }
    return `${Number(value).toFixed(2)}${suffix}`;
}

function formatDb(value) {
    if (value === undefined || value === null || Number.isNaN(value)) {
        return '-';
    }
    return `${Number(value).toFixed(2)} dB`;
}

function normalizeAzimuth(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return 0;
    }
    return ((num % 360) + 360) % 360;
}

function formatAzimuth(value) {
    if (value === undefined || value === null || Number.isNaN(Number(value))) {
        return '-';
    }
    return `${normalizeAzimuth(value).toFixed(1)}°`;
}

function formatWithUnit(value, unit) {
    if (value === undefined || value === null || Number.isNaN(Number(value))) {
        return '-';
    }
    return `${Number(value).toFixed(2)} ${unit}`;
}

function parseNullableNumber(raw) {
    if (raw === undefined || raw === null) {
        return null;
    }
    const text = String(raw).trim();
    if (!text) {
        return null;
    }
    const asNumber = Number(text);
    return Number.isFinite(asNumber) ? asNumber : null;
}

function getTileStatValue(stats, zoom, x, y) {
    if (!stats) {
        return null;
    }
    const bucket = stats[String(zoom)] || stats[zoom];
    if (!bucket) {
        return null;
    }
    const scale = 1 << zoom;
    const normalizedX = ((x % scale) + scale) % scale;
    return bucket[`${normalizedX}/${y}`] ?? null;
}

function createTileLabelUrl(value) {
    if (value === null || value === undefined) {
        return BLANK_TILE_DATA_URL;
    }
    const text = `${value.toFixed(1)} dBµV/m`;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">
        <rect width="256" height="256" fill="rgba(255,255,255,0)"/>
        <rect x="6" y="6" rx="6" ry="6" width="140" height="32" fill="rgba(255,255,255,0.65)"/>
        <text x="16" y="30" font-family="Inter, Arial, sans-serif" font-size="14" fill="#0f172a">${text}</text>
    </svg>`;
    return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function formatLegendValue(value, suffix = '') {
    if (value === undefined || value === null || Number.isNaN(value)) {
        return '-';
    }
    return `${Number(value).toFixed(2)}${suffix}`;
}

function updateTxLegend(data) {
    const legend = document.getElementById('txLegend');
    if (!legend || !data) {
        return;
    }
    const municipality = data.txLocationName
        || data.tx_location_name
        || data.municipality
        || '-';
    const power = data.transmissionPower ?? data.transmission_power;
    const tower = data.towerHeight ?? data.tower_height;
    const elevation = data.txElevation ?? data.tx_site_elevation;

    legend.innerHTML = `
        <div class="tx-legend-title">Transmissor</div>
        <div class="tx-legend-line"><span>Município</span><strong>${municipality}</strong></div>
        <div class="tx-legend-line"><span>Potência</span><strong>${formatLegendValue(power, ' W')}</strong></div>
        <div class="tx-legend-line"><span>Terreno</span><strong>${formatLegendValue(elevation, ' m')}</strong></div>
        <div class="tx-legend-line"><span>Altura torre</span><strong>${formatLegendValue(tower, ' m')}</strong></div>
    `;
    legend.hidden = false;
}

function generateReceiverId() {
    state.rxSequence = (state.rxSequence || 0) + 1;
    return `rx-${Date.now()}-${state.rxSequence}`;
}

function toDataUrl(base64) {
    if (!base64) {
        return null;
    }
    if (base64.startsWith('data:')) {
        return base64;
    }
    return `data:image/png;base64,${base64}`;
}

function toNumeric(value) {
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
}

function mergeReceiverSnapshots(primary = [], fallback = []) {
    const map = new Map();
    const addEntry = (entry, override = false) => {
        if (!entry) return;
        const key = entry.id || `${entry.label || ''}-${entry.lat ?? ''}-${entry.lng ?? ''}`;
        if (!key) return;
        if (!map.has(key) || override) {
            map.set(key, { ...entry });
        }
    };
    fallback.forEach((item) => addEntry(item, false));
    primary.forEach((item) => addEntry(item, true));
    return Array.from(map.values());
}

function buildAssetPreviewUrl(assetId) {
    const slug = getActiveProjectSlug();
    if (!slug || !assetId) {
        return null;
    }
    return `/projects/${encodeURIComponent(slug)}/assets/${encodeURIComponent(assetId)}/preview`;
}

function summaryPayloadFromEntry(entry) {
    const summary = entry.summary || {};
    return {
        municipality: summary.municipality || null,
        distance_km: summary.distanceValue ?? null,
        distance: summary.distance || null,
        field_dbuv_m: summary.fieldValue ?? null,
        field: summary.field || null,
        elevation_m: summary.elevationValue ?? null,
        elevation: summary.elevation || null,
        bearing_deg: summary.bearingValue ?? null,
        bearing: summary.bearing || null,
    };
}

function persistTxLocation(latLng) {
    const payload = {
        latitude: Number(latLng.lat().toFixed(6)),
        longitude: Number(latLng.lng().toFixed(6)),
    };
    const projectSlug = getActiveProjectSlug();
    if (projectSlug) {
        payload.projectSlug = projectSlug;
    }
    return fetch('/tx-location', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then((response) => response.json().then((json) => {
            if (!response.ok) {
                throw new Error(json.error || 'Falha ao salvar a localização da TX.');
            }
            return json;
        }))
        .then((data) => {
            if (!state.txData) {
                state.txData = {};
            }
            state.txData.latitude = payload.latitude;
            state.txData.longitude = payload.longitude;
            if (data.municipality) {
                state.txData.txLocationName = data.municipality;
            }
            if (data.elevation !== undefined && data.elevation !== null) {
                state.txData.txElevation = data.elevation;
            }
            updateTxSummary(state.txData);
            showToast('Localização da TX atualizada.', false);
            return data;
        })
        .catch((error) => {
            console.error(error);
            showToast(error.message || 'Não foi possível salvar a nova posição da TX.', true);
            throw error;
        });
}

function getRxLabel(index) {
    return `RX ${index + 1}`;
}

function openRxLegend(entry) {
    if (!state.rxInfoWindow || !entry) {
        return;
    }
    const index = state.rxEntries.indexOf(entry);
    if (index < 0) {
        return;
    }
    const summary = entry.summary || {};
    const municipalityLine = summary.municipality ? `<div>Município: ${summary.municipality}</div>` : '';
    const populationLine = summary.population ? `<div>População: ${Number(summary.population).toLocaleString('pt-BR')} ${summary.population_year ? `(${summary.population_year})` : ''}</div>` : '';
    const content = `
        <div class="rx-legend">
            <strong>${entry.label || getRxLabel(index)}</strong>
            <div>Nível: ${summary.field || '-'}</div>
            <div>Altitude: ${summary.elevation || '-'}</div>
            ${municipalityLine}
            ${populationLine}
        </div>
    `;
    state.rxInfoWindow.setContent(content);
    state.rxInfoWindow.open({
        map: state.map,
        anchor: entry.marker,
    });
}

function getActiveProjectSlug() {
    if (window.coverageProjectSlug) {
        return window.coverageProjectSlug;
    }
    const container = document.getElementById('coverageMapContainer');
    if (container && container.dataset.project) {
        return container.dataset.project;
    }
    return null;
}

function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
            const result = reader.result;
            if (typeof result === 'string') {
                const base64 = result.includes(',') ? result.split(',')[1] : result;
                resolve(base64);
            } else {
                reject(new Error('Falha ao converter imagem.'));
            }
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
    });
}

function updateRadiusLabel() {
    const radiusInput = document.getElementById('radiusInput');
    const radiusValue = document.getElementById('radiusValue');
    radiusValue.textContent = `${radiusInput.value} km`;
    refreshDirectionGuide();
}

function updateTiltLabel(value) {
    document.getElementById('tiltValue').textContent = `${Number(value).toFixed(1)}°`;
}

function updateDirectionLabel(value) {
    const display = document.getElementById('directionValue');
    if (!display) {
        return;
    }
    display.textContent = `${normalizeAzimuth(value).toFixed(1)}°`;
}

function clearDirectionLine() {
    if (state.directionLine) {
        state.directionLine.setMap(null);
        state.directionLine = null;
    }
}

function getActiveRadiusKm() {
    const lastRadius = state.coverageData?.requested_radius_km || state.coverageData?.radius || null;
    if (Number.isFinite(lastRadius)) {
        return Number(lastRadius);
    }
    const radiusInput = document.getElementById('radiusInput');
    if (radiusInput) {
        const sliderValue = Number(radiusInput.value);
        if (Number.isFinite(sliderValue) && sliderValue > 0) {
            return sliderValue;
        }
    }
    return 10;
}

function updateDirectionGuide(direction) {
    if (!state.map || !state.txCoords) {
        return;
    }
    const heading = normalizeAzimuth(direction);
    const radiusKm = Math.max(getActiveRadiusKm(), 1);
    const lineLength = Math.max(radiusKm * 1000, 500);
    const endPoint = google.maps.geometry.spherical.computeOffset(state.txCoords, lineLength, heading);
    const path = [state.txCoords, endPoint];
    clearDirectionLine();
    state.directionLine = new google.maps.Polyline({
        map: state.map,
        path,
        strokeColor: '#ef6c00',
        strokeOpacity: 0.95,
        strokeWeight: 2,
        icons: [{
            icon: {
                path: 'M 0,-1 0,1',
                strokeOpacity: 1,
                scale: 3,
            },
            offset: '0',
            repeat: '12px',
        }],
    });
}

function refreshDirectionGuide() {
    const direction = state.txData?.antennaDirection;
    if (direction === undefined || direction === null) {
        clearDirectionLine();
        return;
    }
    updateDirectionGuide(direction);
}

function updateTxSummary(data) {
    document.getElementById('txLat').textContent = formatNumber(data.latitude, '°');
    document.getElementById('txLng').textContent = formatNumber(data.longitude, '°');
    document.getElementById('txFreq').textContent = data.frequency ? `${Number(data.frequency).toFixed(2)} MHz` : '-';
    document.getElementById('txModel').textContent = data.propagationModel || '-';
    updateTiltLabel(data.antennaTilt || 0);
    document.getElementById('tiltControl').value = data.antennaTilt ?? 0;
    document.getElementById('txDirection').textContent =
        data.antennaDirection === undefined || data.antennaDirection === null
            ? '-'
            : formatAzimuth(data.antennaDirection);
    const directionControl = document.getElementById('directionControl');
    if (directionControl) {
        const directionValue = data.antennaDirection === undefined || data.antennaDirection === null
            ? 0
            : normalizeAzimuth(data.antennaDirection);
        directionControl.value = directionValue;
        updateDirectionLabel(directionValue);
    }

    const municipalityEl = document.getElementById('txMunicipio');
    if (municipalityEl) {
        municipalityEl.textContent = data.txLocationName
            || data.tx_location_name
            || data.municipality
            || '-';
    }

    const elevationEl = document.getElementById('txElevation');
    if (elevationEl) {
        const elevationValue = data.txElevation ?? data.tx_site_elevation;
        elevationEl.textContent = elevationValue !== undefined && elevationValue !== null
            ? formatNumber(elevationValue, ' m')
            : '-';
    }

    const climateInfoEl = document.getElementById('txClimateInfo');
    if (climateInfoEl) {
        const message = data.location_status || data.locationStatus;
        if (message) {
            climateInfoEl.textContent = message;
        } else if (data.climateUpdatedAt) {
            const updatedDate = new Date(data.climateUpdatedAt);
            climateInfoEl.textContent = `Clima atualizado em ${updatedDate.toLocaleString()}`;
        } else {
            climateInfoEl.textContent = 'Clima não ajustado para esta localização';
        }
    }

    updateTxLegend(data);
}

function heightToColor(ratio) {
    const clamped = Math.min(Math.max(Number(ratio) || 0, 0), 1);
    const hue = 210 - clamped * 210; // blue -> red
    return `hsl(${hue}, 85%, 55%)`;
}

function clearRt3dLayer() {
    state.rt3dLayer.forEach((shape) => shape.setMap(null));
    state.rt3dLayer = [];
    if (state.sceneInfoWindow) {
        state.sceneInfoWindow.close();
    }
}

function clearRt3dRays() {
    state.rt3dRaysLayer.forEach((line) => line.setMap(null));
    state.rt3dRaysLayer = [];
}

function updateRt3dPanel(scene) {
    const panel = document.getElementById('rt3dPanel');
    if (!panel) {
        return;
    }
    const toggleBtn = document.getElementById('toggleRt3dLayer');
    const refreshBtn = document.getElementById('refreshRt3dLayer');
    const toggleRaysBtn = document.getElementById('toggleRt3dRays');
    const openViewerBtn = document.getElementById('openRt3dViewer');
    const downloadGeoBtn = document.getElementById('downloadRt3dGeojson');
    const hasScenePoints = scene && Array.isArray(scene.points) && scene.points.length > 0;
    const hasRays = Array.isArray(state.coverageData?.rt3dRays) && state.coverageData.rt3dRays.length > 0;
    const sceneData = hasScenePoints
        ? scene
        : (state.rt3dScene || state.coverageData?.rt3dScene || null);
    const settings = state.coverageData?.rt3dSettings || null;

    if (!hasScenePoints && !hasRays) {
        panel.hidden = true;
        if (toggleBtn) {
            toggleBtn.disabled = true;
            toggleBtn.textContent = 'Ocultar malha urbana';
        }
        if (refreshBtn) {
            refreshBtn.disabled = false;
        }
        if (toggleRaysBtn) {
            toggleRaysBtn.disabled = true;
            toggleRaysBtn.textContent = 'Ocultar raios';
        }
        if (openViewerBtn) {
            openViewerBtn.disabled = true;
        }
        if (downloadGeoBtn) {
            downloadGeoBtn.disabled = true;
        }
        return;
    }

    panel.hidden = false;
    const friendlySource = (settings?.building_source === 'google')
        ? 'Google Photorealistic 3D'
        : (settings?.building_source === 'osm')
            ? 'OSM / Overpass'
            : (sceneData?.source === 'osm-overpass' ? 'OSM / Overpass' : (sceneData?.source || 'Automático'));

    const sourceEl = document.getElementById('rt3dSource');
    const countEl = document.getElementById('rt3dCount');
    const medianEl = document.getElementById('rt3dMedian');
    const radiusEl = document.getElementById('rt3dRadius');
    const statusEl = document.getElementById('rt3dStatus');

    if (sourceEl) sourceEl.textContent = friendlySource;
    if (countEl) {
        const featureCount = sceneData?.feature_count
            ?? (Array.isArray(sceneData?.points) ? sceneData.points.length : 0);
        countEl.textContent = featureCount || '-';
    }
    if (medianEl) {
        const value = Number(sceneData?.median_height);
        medianEl.textContent = Number.isFinite(value) ? `${value.toFixed(1)} m` : '-';
    }
    if (radiusEl) {
        const radiusValue = Number(sceneData?.radius_km);
        radiusEl.textContent = Number.isFinite(radiusValue) ? `${radiusValue.toFixed(2)} km` : '-';
    }
    if (statusEl) {
        const rayCount = state.coverageData?.rt3dRays?.length || sceneData?.rays?.length || 0;
        const diagnostics = sceneData?.diagnostics || scene?.diagnostics;
        if (diagnostics) {
            const parts = [];
            if (diagnostics.mode) {
                parts.push(`Modo ${diagnostics.mode}`);
            }
            if (diagnostics.samples) {
                parts.push(`${diagnostics.samples} amostras`);
            }
            if (rayCount) {
                parts.push(`${rayCount} raios plotados`);
            }
            statusEl.textContent = parts.length
                ? `Diagnóstico: ${parts.join(' · ')}`
                : 'Malha processada recentemente.';
        } else {
            statusEl.textContent = rayCount
                ? `Malha processada · ${rayCount} raios disponíveis`
                : 'Malha processada recentemente.';
        }
        const extras = [];
        if (friendlySource) {
            extras.push(`Fonte: ${friendlySource}`);
        }
        if (settings?.ray_step_m) {
            extras.push(`Passo do raio: ${Number(settings.ray_step_m).toFixed(1)} m`);
        }
        if (settings?.minimum_clearance_m) {
            extras.push(`Clearance min: ${Number(settings.minimum_clearance_m).toFixed(1)} m`);
        }
        if (extras.length) {
            statusEl.textContent = `${statusEl.textContent} · ${extras.join(' · ')}`;
        }
    }

    if (toggleBtn) {
        toggleBtn.disabled = state.rt3dLayer.length === 0;
        toggleBtn.textContent = state.isRt3dLayerVisible ? 'Ocultar malha urbana' : 'Mostrar malha urbana';
    }
    if (refreshBtn) {
        refreshBtn.disabled = false;
    }
    if (toggleRaysBtn) {
        toggleRaysBtn.disabled = !hasRays;
        toggleRaysBtn.textContent = state.isRt3dRaysVisible ? 'Ocultar raios' : 'Mostrar raios';
    }
    if (openViewerBtn) {
        openViewerBtn.disabled = false;
    }
    if (downloadGeoBtn) {
        downloadGeoBtn.disabled = false;
    }
}

function setRt3dLayerVisibility(visible) {
    state.isRt3dLayerVisible = Boolean(visible);
    state.rt3dLayer.forEach((shape) => shape.setMap(state.isRt3dLayerVisible ? state.map : null));
    updateRt3dPanel(state.rt3dScene);
}

function setRt3dRaysVisibility(visible) {
    state.isRt3dRaysVisible = Boolean(visible);
    state.rt3dRaysLayer.forEach((line) => line.setMap(state.isRt3dRaysVisible ? state.map : null));
    updateRt3dPanel(state.rt3dScene);
}

function renderRt3dRays(rays) {
    clearRt3dRays();
    if (!state.map || !Array.isArray(rays) || !rays.length) {
        return;
    }
    const MAX_RAYS = 250;
    const effectiveRays = rays.slice(0, MAX_RAYS);
    const infoWindow = state.sceneInfoWindow || new google.maps.InfoWindow();
    state.sceneInfoWindow = infoWindow;
    state.rt3dRaysLayer = effectiveRays.map((ray) => {
        const path = Array.isArray(ray.path)
            ? ray.path
                .map((point) => {
                    const lat = Number(point.lat);
                    const lng = Number(point.lng);
                    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
                        return null;
                    }
                    return new google.maps.LatLng(lat, lng);
                })
                .filter(Boolean)
            : [];
        if (path.length < 2) {
            return null;
        }
        const color = colorFromQuality(ray.quality_db, ray.mode);
        const polyline = new google.maps.Polyline({
            map: state.isRt3dRaysVisible ? state.map : null,
            path,
            geodesic: true,
            strokeColor: color,
            strokeOpacity: 0.9,
            strokeWeight: ray.mode === 'reflection' ? 3.5 : 2.5,
        });
        polyline.addListener('click', (event) => {
            const quality = ray.quality_db !== undefined && ray.quality_db !== null
                ? `${Number(ray.quality_db).toFixed(2)} dB`
                : 'N/D';
            const content = `
                <div class="rt3d-ray-tooltip">
                    <strong>Modo:</strong> ${ray.mode || '—'}<br>
                    <strong>Qualidade:</strong> ${quality}<br>
                    ${ray.height_m ? `<strong>Altura impacto:</strong> ${Number(ray.height_m).toFixed(1)} m` : ''}
                </div>
            `;
            infoWindow.setContent(content);
            infoWindow.setPosition(event.latLng || path[path.length - 1]);
            infoWindow.open({
                map: state.map,
                anchor: null,
                shouldFocus: false,
            });
        });
        return polyline;
    }).filter(Boolean);
}

function renderRt3dScene(scene) {
    clearRt3dLayer();
    clearRt3dRays();
    state.rt3dScene = scene || null;
    if (state.coverageData?.rt3dSettings) {
        state.rt3dSettings = state.coverageData.rt3dSettings;
    }
    if (!scene || !state.map || !Array.isArray(scene.points) || !scene.points.length) {
        updateRt3dPanel(null);
        renderRt3dRays(state.coverageData?.rt3dRays || scene?.rays);
        return;
    }
    const heights = scene.points
        .map((pt) => Number(pt.height_m))
        .filter((val) => Number.isFinite(val) && val > 0);
    const maxHeight = Math.max(...heights, 1);
    const MAX_RT3D_POINTS = 250;
    let pointSet = Array.isArray(scene.points) ? scene.points.slice() : [];
    if (pointSet.length > MAX_RT3D_POINTS) {
        const stride = Math.ceil(pointSet.length / MAX_RT3D_POINTS);
        pointSet = pointSet.filter((_, idx) => idx % stride === 0);
    }

    state.rt3dLayer = pointSet.map((pt) => {
        const lat = Number(pt.lat);
        const lon = Number(pt.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
            return null;
        }
        const height = Math.max(0, Number(pt.height_m) || 0);
        const position = new google.maps.LatLng(lat, lon);
        const circle = new google.maps.Circle({
            center: position,
            radius: Math.max(20, Math.min(90, 12 + height)),
            strokeOpacity: 0,
            fillOpacity: 0.55,
            fillColor: heightToColor(height / maxHeight),
            map: state.isRt3dLayerVisible ? state.map : null,
        });
        circle.addListener('click', () => {
            if (!state.sceneInfoWindow) {
                return;
            }
            state.sceneInfoWindow.setContent(
                `<strong>${height.toFixed(1)} m</strong><br>Lat: ${lat.toFixed(5)}<br>Lon: ${lon.toFixed(5)}`
            );
            state.sceneInfoWindow.setPosition(position);
            state.sceneInfoWindow.open({ map: state.map, anchor: null, shouldFocus: false });
        });
        return circle;
    }).filter(Boolean);
    updateRt3dPanel(scene);
    renderRt3dRays(state.coverageData?.rt3dRays || scene?.rays);
}

function updateGainSummary(gainComponents, scale) {
    if (!gainComponents) {
        document.getElementById('gainBase').textContent = '-';
        document.getElementById('gainHorizontal').textContent = '-';
        document.getElementById('gainVertical').textContent = '-';
        document.getElementById('fieldScale').textContent = '-';
        return;
    }
    document.getElementById('gainBase').textContent = formatDb(gainComponents.base_gain_dbi || 0);

    if (gainComponents.horizontal_adjustment_db_min !== undefined) {
        const min = formatDb(gainComponents.horizontal_adjustment_db_min);
        const max = formatDb(gainComponents.horizontal_adjustment_db_max);
        document.getElementById('gainHorizontal').textContent = `${min} / ${max}`;
    }

    document.getElementById('gainVertical').textContent = formatDb(gainComponents.vertical_adjustment_db);

    if (scale) {
        document.getElementById('fieldScale').textContent =
            `${formatNumber(scale.min)} – ${formatNumber(scale.max)} dBµV/m`;
    }
}

function updateCenterSummary(metrics) {
    const data = metrics || {};
    document.getElementById('centerLoss').textContent = formatWithUnit(data.combined_loss_center_db, 'dB');
    document.getElementById('centerGain').textContent = formatWithUnit(data.effective_gain_center_db, 'dB');

    const pathInfo = document.getElementById('pathTypeInfo');
    if (pathInfo) {
        pathInfo.textContent = data.path_type ? `Classe de propagação no centro: ${data.path_type}` : '';
    }

    const distanceValue = data.distance_center_km ?? data.radius_km ?? null;
    document.getElementById('centerDistance').textContent = formatWithUnit(distanceValue, 'km');
    const summaryElement = document.getElementById('centerPath');
    if (summaryElement) {
        summaryElement.textContent = data.path_type || '-';
    }
}

function updateLossSummary(lossComponents) {
    const mapping = {
        L_b0p: 'loss-L_b0p',
        L_bd: 'loss-L_bd',
        L_bs: 'loss-L_bs',
        L_ba: 'loss-L_ba',
        L_b: 'loss-L_b',
        L_b_corr: 'loss-L_b_corr',
    };

    Object.entries(mapping).forEach(([key, elementId]) => {
        const element = document.getElementById(elementId);
        if (!element) {
            return;
        }
        const component = lossComponents ? lossComponents[key] : null;
        if (!component) {
            element.textContent = '-';
            return;
        }
        const centerText = component.center !== undefined && component.center !== null
            ? formatDb(component.center)
            : '-';
        if (component.min !== undefined && component.max !== undefined) {
            element.textContent = `${centerText} (${formatDb(component.min)} – ${formatDb(component.max)})`;
        } else {
            element.textContent = centerText;
        }
    });
}

function refreshReceiverSummaries() {
    if (!state.rxEntries.length || !state.txCoords) {
        updateLinkSummary({});
        return;
    }
    state.rxEntries.forEach((entry, idx) => {
        const position = entry.marker.getPosition();
        computeReceiverSummary(position).then((summary) => {
            entry.summary = summary;
            if (idx === state.selectedRxIndex) {
                updateLinkSummary(summary);
            }
            renderRxList();
        });
    });
}

function serializeReceivers() {
    return state.rxEntries
        .map((entry, index) => {
            if (!entry || !entry.marker) {
                return null;
            }
            const position = entry.marker.getPosition();
            if (!position) {
                return null;
            }
            const lat = Number(position.lat());
            const lng = Number(position.lng());
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
                return null;
            }
            const summary = entry.summary || {};
            const payload = {
                id: entry.id,
                lat: Number(lat.toFixed(7)),
                lng: Number(lng.toFixed(7)),
                label: entry.label || getRxLabel(index),
                municipality: summary.municipality || null,
                distance: summary.distance || null,
                distance_km: summary.distanceValue ?? null,
                bearing: summary.bearing || null,
                bearing_deg: summary.bearingValue ?? null,
                field: summary.fieldValue ?? null,
                field_text: summary.field || null,
                elevation: summary.elevationValue ?? null,
                elevation_text: summary.elevation || null,
                profile: entry.profile || null,
                profile_meta: entry.profileMeta || null,
                profile_asset_id: entry.profileAssetId || null,
                profile_asset_path: entry.profileAssetPath || null,
                profile_asset_url: entry.profileAssetUrl || null,
            };
            if (summary.populationValue !== undefined && summary.populationValue !== null) {
                payload.population = summary.populationValue;
                payload.population_text = summary.population || null;
                payload.population_year = summary.populationYear ?? null;
            }
            if (entry.receiverRecord?.ibge) {
                payload.ibge = entry.receiverRecord.ibge;
            }
            return payload;
        })
        .filter(Boolean);
}

function buildSummaryFromReceiver(receiver) {
    const summary = {};
    const distanceValue = parseNullableNumber(receiver.distance_km ?? receiver.distanceValue ?? receiver.distance);
    if (Number.isFinite(distanceValue)) {
        summary.distanceValue = distanceValue;
        summary.distance = receiver.distance || `${distanceValue.toFixed(2)} km`;
    } else if (receiver.distance_text) {
        summary.distance = receiver.distance_text;
    }
    const bearingValue = parseNullableNumber(receiver.bearing_deg ?? receiver.bearingValue ?? receiver.bearing);
    if (Number.isFinite(bearingValue)) {
        summary.bearingValue = bearingValue;
        summary.bearing = `${bearingValue.toFixed(1)}°`;
    } else if (receiver.bearing) {
        summary.bearing = receiver.bearing;
    }
    const fieldValue = parseNullableNumber(receiver.field ?? receiver.field_value ?? receiver.field_dbuv_m);
    if (Number.isFinite(fieldValue)) {
        summary.fieldValue = fieldValue;
        summary.field = `${fieldValue.toFixed(1)} dBµV/m`;
    } else if (receiver.field_text) {
        summary.field = receiver.field_text;
    }
    const elevationValue = parseNullableNumber(receiver.elevation ?? receiver.elevation_m ?? receiver.elevationValue);
    if (Number.isFinite(elevationValue)) {
        summary.elevationValue = elevationValue;
        summary.elevation = `${elevationValue.toFixed(1)} m`;
    } else if (receiver.elevation_text) {
        summary.elevation = receiver.elevation_text;
    }
    summary.municipality = receiver.municipality
        || receiver.location?.municipality
        || receiver.summary?.municipality
        || null;
    if (receiver.location?.state_code) {
        summary.state = receiver.location.state_code;
    } else if (receiver.state) {
        summary.state = receiver.state;
    }
    if (receiver.ibge) {
        summary.ibge = receiver.ibge;
    }
    const populationCandidate = parseNullableNumber(
        receiver.population
        ?? receiver.population_value
        ?? receiver.demographics?.total
        ?? receiver.summary?.populationValue
    );
    if (Number.isFinite(populationCandidate)) {
        summary.populationValue = Number(populationCandidate);
        try {
            summary.population = Number(populationCandidate).toLocaleString('pt-BR');
        } catch (error) {
            summary.population = String(populationCandidate);
        }
    } else if (receiver.population_text) {
        summary.population = receiver.population_text;
    }
    summary.populationYear = receiver.population_year
        || receiver.demographics?.period
        || receiver.summary?.populationYear
        || null;
    return summary;
}

function restoreReceivers(receivers) {
    clearReceivers({ persist: false });
    if (!Array.isArray(receivers) || !receivers.length || !state.map) {
        return;
    }
    receivers.forEach((receiver) => {
        const lat = parseNullableNumber(receiver.lat);
        const lng = parseNullableNumber(receiver.lng);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
            return;
        }
        const position = new google.maps.LatLng(lat, lng);
        const presetSummary = buildSummaryFromReceiver(receiver);
        createRxMarker(position, {
            selectOnCreate: false,
            presetLabel: receiver.label || null,
            presetSummary,
            presetId: receiver.id || null,
            presetProfile: receiver.profile || null,
            profileAssetUrl: receiver.profile_asset_url
                || (receiver.profile_asset_id ? buildAssetPreviewUrl(receiver.profile_asset_id) : null),
            profileAssetId: receiver.profile_asset_id || null,
            profileAssetPath: receiver.profile_asset_path || null,
            profileMeta: receiver.profile_meta || null,
            profileThumbnail: receiver.profile_image ? toDataUrl(receiver.profile_image) : null,
            autoProfile: !(receiver.profile || receiver.profile_asset_id),
        });
    });
    state.selectedRxIndex = null;
    updateLinkSummary({});
    renderRxList();
}

function showToast(message, isError = false) {
    const card = document.getElementById('mapTooltip');
    if (!card) return;
    card.innerHTML = `<h4>${isError ? 'Atenção' : 'Cobertura'}</h4><p>${message}</p>`;
    card.hidden = false;
    setTimeout(() => {
        card.hidden = true;
    }, 3600);
}

function initRxContextMenu() {
    const menu = document.getElementById('rxContextMenu');
    if (!menu) return;
    state.rxContextMenu = menu;
    menu.addEventListener('click', (event) => {
        event.stopPropagation();
    });
    const actionButtons = menu.querySelectorAll('button[data-action]');
    actionButtons.forEach((button) => {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            const action = button.dataset.action;
            const entry = state.rxContextMenuEntry;
            hideRxContextMenu();
            if (!entry) return;
            if (action === 'move') {
                startReceiverRelocation(entry);
            } else if (action === 'profile') {
                requestProfileGeneration(entry, { force: false, openModal: true });
            } else if (action === 'delete') {
                const index = state.rxEntries.indexOf(entry);
                if (index >= 0) {
                    removeRx(index);
                }
            }
        });
    });
    document.addEventListener('click', (event) => {
        if (!state.rxContextMenu || state.rxContextMenu.hidden) return;
        if (state.rxContextMenu.contains(event.target)) return;
        hideRxContextMenu();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            hideRxContextMenu();
            cancelReceiverRelocation();
        }
    });
}

function showRxContextMenu(entry, mapEvent) {
    const menu = state.rxContextMenu;
    if (!menu) return;
    state.rxContextMenuEntry = entry;
    const container = document.getElementById('coverageMapContainer');
    const rect = container ? container.getBoundingClientRect() : { left: 0, top: 0 };
    let clientX = 0;
    let clientY = 0;
    if (mapEvent && mapEvent.domEvent) {
        clientX = mapEvent.domEvent.clientX;
        clientY = mapEvent.domEvent.clientY;
    } else if (mapEvent && typeof mapEvent.clientX === 'number') {
        clientX = mapEvent.clientX;
        clientY = mapEvent.clientY;
    }
    menu.style.left = `${clientX - rect.left}px`;
    menu.style.top = `${clientY - rect.top}px`;
    menu.hidden = false;
}

function hideRxContextMenu() {
    if (state.rxContextMenu) {
        state.rxContextMenu.hidden = true;
    }
    state.rxContextMenuEntry = null;
}

function startReceiverRelocation(entry) {
    state.rxMoveCandidate = entry;
    if (state.map) {
        state.map.setOptions({ draggableCursor: 'crosshair' });
    }
    showToast(`Clique no mapa para reposicionar ${entry.label}.`);
}

function cancelReceiverRelocation() {
    state.rxMoveCandidate = null;
    if (state.map) {
        state.map.setOptions({ draggableCursor: null });
    }
}

function completeReceiverRelocation(entry, latLng) {
    if (!entry || !latLng) return;
    entry.marker.setPosition(latLng);
    cancelReceiverRelocation();
    entry.summary = null;
    computeReceiverSummary(latLng).then((summary) => {
        entry.summary = summary;
        if (state.selectedRxIndex === state.rxEntries.indexOf(entry)) {
            updateLinkSummary(summary);
        }
        renderRxList();
    });
    if (state.coverageData) {
        state.coverageData.receivers = serializeReceivers();
    }
    requestProfileGeneration(entry, { force: true, silent: true });
}

function setProfileLoading(isLoading) {
    profileLoading = Boolean(isLoading);
    const spinner = document.getElementById('profileSpinner');
    if (spinner) {
        spinner.hidden = !profileLoading;
    }
    const button = document.getElementById('btnGenerateProfile');
    if (button) {
        button.disabled = profileLoading || state.selectedRxIndex === null;
    }
}

function setCoverageLoading(isLoading) {
    const spinner = document.getElementById('coverageSpinner');
    if (spinner) {
        spinner.hidden = !isLoading;
    }
    const button = document.getElementById('btnGenerateCoverage');
    if (button) {
        button.disabled = isLoading;
    }
    const toggleElements = document.querySelectorAll('[data-disabled-during-coverage]');
    toggleElements.forEach((element) => {
        element.disabled = isLoading;
    });
}

function fetchMunicipality(latLng) {
    const lat = Number(latLng.lat());
    const lng = Number(latLng.lng());
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        return Promise.resolve(null);
    }
    const params = new URLSearchParams({ lat, lon: lng });
    return fetch(`/reverse-geocode?${params.toString()}`)
        .then((response) => response.json().then((json) => {
            if (!response.ok) {
                throw new Error(json.error || 'Falha ao buscar município.');
            }
            return {
                label: json.municipality || null,
                ibge_code: json.ibge_code || null,
                state: json.state || null,
                population: json.population ?? null,
                population_year: json.population_year ?? null,
            };
        }))
        .catch(() => null);
}

function ensureElevationService() {
    if (!state.elevationService) {
        state.elevationService = new google.maps.ElevationService();
    }
    return state.elevationService;
}

function restoreCoverageFromProject(data) {
    if (!data || !data.lastCoverage) {
        return;
    }
    const last = data.lastCoverage;
    if (last.center_metrics) {
        updateGainSummary(null, null);
        document.getElementById('centerLoss').textContent = formatDb(last.center_metrics.combined_loss_center_db);
    }

    const radiusInput = document.getElementById('radiusInput');
    if (radiusInput && last.radius_km) {
        radiusInput.value = last.radius_km;
        updateRadiusLabel();
    }

    if (last.center) {
        setTxCoords(new google.maps.LatLng(last.center.lat, last.center.lng), { pan: false });
    }

    if (last.asset_id) {
        applyCoverageOverlay({
            images: {
                dbuv: { image: last.preview, colorbar: null },
            },
            bounds: data.bounds,
            requested_radius_km: last.radius_km,
            center: data.center,
        });
    }
}

function removeTileOverlay() {
    if (!state.map) {
        return;
    }
    const overlays = state.map.overlayMapTypes;
    if (!overlays) {
        state.tileOverlayLayer = null;
        state.tileLabelLayer = null;
        return;
    }
    for (let i = overlays.getLength() - 1; i >= 0; i -= 1) {
        const layer = overlays.getAt(i);
        if (layer === state.tileOverlayLayer || layer === state.tileLabelLayer) {
            overlays.removeAt(i);
        }
    }
    state.tileOverlayLayer = null;
    state.tileLabelLayer = null;
}

function _isValidTileConfig(tileConfig) {
    if (!tileConfig) return false;
    const template = tileConfig.url_template || tileConfig.urlTemplate || '';
    const assetId = tileConfig.asset_id || tileConfig.assetId;
    if (!assetId || assetId === 'None') return false;
    if (!template || template.includes('/None/')) return false;
    return true;
}

function _sanitizeTileConfig(tileConfig) {
    return _isValidTileConfig(tileConfig) ? {
        asset_id: tileConfig.asset_id || tileConfig.assetId,
        url_template: tileConfig.url_template || tileConfig.urlTemplate,
        min_zoom: tileConfig.min_zoom ?? tileConfig.minZoom,
        max_zoom: tileConfig.max_zoom ?? tileConfig.maxZoom,
        stats: tileConfig.stats || tileConfig.tile_stats || null,
        bounds: tileConfig.bounds || null,
    } : null;
}

function applyTileOverlay(tileConfig) {
    if (!state.map) {
        return false;
    }
    const sanitizedTileConfig = _sanitizeTileConfig(tileConfig);
    if (!sanitizedTileConfig) {
        removeTileOverlay();
        if (state.coverageData) {
            state.coverageData.tiles = null;
        }
        return false;
    }
    const template = sanitizedTileConfig.url_template;

    const minZoom = Number(sanitizedTileConfig.min_zoom ?? 3);
    const maxZoom = Number(sanitizedTileConfig.max_zoom ?? 21);

    removeTileOverlay();

    const layer = new google.maps.ImageMapType({
        getTileUrl: (coord, zoom) => {
            const scale = 1 << zoom;
            const normalizedX = ((coord.x % scale) + scale) % scale;
            if (coord.y < 0 || coord.y >= scale) {
                return '';
            }
            return template
                .replace('{z}', zoom)
                .replace('{x}', normalizedX)
                .replace('{y}', coord.y);
        },
        tileSize: new google.maps.Size(256, 256),
        opacity: state.overlayOpacity,
        name: 'CoverageHeatmap',
        minZoom,
        maxZoom,
    });

    state.map.overlayMapTypes.push(layer);
    state.tileOverlayLayer = layer;

    const statsPayload = tileConfig.stats || tileConfig.tile_stats;
    if (statsPayload && Object.keys(statsPayload).length) {
        const statsLayer = new google.maps.ImageMapType({
            getTileUrl: (coord, zoom) => {
            const value = getTileStatValue(sanitizedTileConfig.stats, zoom, coord.x, coord.y);
            return createTileLabelUrl(value);
        },
        tileSize: new google.maps.Size(256, 256),
        opacity: 1,
        minZoom,
        maxZoom,
    });
    state.map.overlayMapTypes.push(statsLayer);
    state.tileLabelLayer = statsLayer;
    } else {
        state.tileLabelLayer = null;
    }
    if (state.coverageData) {
        state.coverageData.tiles = sanitizedTileConfig;
    }
    return true;
}

function clearCoverageOverlay() {
    removeTileOverlay();
    if (state.coverageOverlay) {
        state.coverageOverlay.setMap(null);
        state.coverageOverlay = null;
    }
    if (state.radiusCircle) {
        state.radiusCircle.setMap(null);
        state.radiusCircle = null;
    }
}

function applyCoverageOverlay(response) {
    clearCoverageOverlay();

    if (!state.map) return;

    let overlayImage = response.image || null;
    let colorbarImage = response.colorbar || null;

    const tileConfig = response.tiles || response.tile_layer || null;
    const overlayFromTiles = applyTileOverlay(tileConfig);

    if (response.images) {
        const availableUnits = Object.keys(response.images);

        let preferredUnit = state.coverageUnit && response.images[state.coverageUnit]
            ? state.coverageUnit
            : null;

        if (!preferredUnit && response.scale?.default_unit && response.images[response.scale.default_unit]) {
            preferredUnit = response.scale.default_unit;
        }

        if (!preferredUnit && availableUnits.length) {
            preferredUnit = availableUnits[0];
        }

        if (preferredUnit) {
            const unitPayload = response.images[preferredUnit];
            if (unitPayload) {
                if (!overlayImage) {
                    overlayImage = unitPayload.image;
                }
                if (!colorbarImage) {
                    colorbarImage = unitPayload.colorbar;
                }
                state.coverageUnit = preferredUnit;
            }
        }
    }

    if (!overlayFromTiles) {
        const bounds = response.bounds;
        if (!bounds) {
            console.warn('Resposta sem limites de cobertura.', response);
            return;
        }
        if (!overlayImage) {
            console.warn('Resposta sem imagem de cobertura disponível.', response);
            return;
        }

        const overlayBounds = new google.maps.LatLngBounds(
            new google.maps.LatLng(bounds.south, bounds.west),
            new google.maps.LatLng(bounds.north, bounds.east),
        );

        const overlay = new google.maps.GroundOverlay(
            `data:image/png;base64,${overlayImage}`,
            overlayBounds,
            { opacity: state.overlayOpacity },
        );
        overlay.setMap(state.map);
        state.coverageOverlay = overlay;

        overlay.addListener('click', (event) => {
            if (!event || !event.latLng || !state.txCoords) return;
            createRxMarker(event.latLng);
        });
    }

    // Colorbar lateral
    if (colorbarImage) {
        const card = document.getElementById('colorbarCard');
        const img = document.getElementById('colorbarImage');
        img.src = `data:image/png;base64,${colorbarImage}`;
        card.hidden = false;
    } else {
        const card = document.getElementById('colorbarCard');
        if (card) {
            card.hidden = true;
        }
    }

    // Desenha o círculo de raio (apenas visual)
    if (response.requested_radius_km && response.center) {
        const centerLatLng = new google.maps.LatLng(response.center.lat, response.center.lng);

        state.radiusCircle = new google.maps.Circle({
            map: state.map,
            center: centerLatLng,
            radius: response.requested_radius_km * 1000,
            strokeColor: '#0d6efd',
            strokeOpacity: 0.4,
            strokeWeight: 2,
            fillColor: '#0d6efd',
            fillOpacity: 0.1,
            clickable: false, // <<< não intercepta clique
        });
    }

    refreshDirectionGuide();
}

async function loadCoverageOverlay(lastCoverage) {
    if (!lastCoverage || !lastCoverage.asset_id) {
        throw new Error('Nenhuma mancha de cobertura disponível para este projeto.');
    }
    if (!window.coverageProjectSlug) {
        throw new Error('Projeto não informado para restaurar a cobertura.');
    }

    let slug = lastCoverage.project_slug
        || lastCoverage.projectSlug
        || window.coverageProjectSlug
        || null;
    if (!slug) {
        throw new Error('Projeto não definido para restaurar a cobertura.');
    }
    const summaryId = lastCoverage.json_asset_id;
    let summary = null;

    if (summaryId) {
        try {
            const summaryResponse = await fetch(
                `/projects/${encodeURIComponent(slug)}/assets/${encodeURIComponent(summaryId)}/preview`,
                { headers: { Accept: 'application/json' } },
            );
            if (summaryResponse.ok) {
                summary = await summaryResponse.json();
                if (!slug && summary?.project_slug) {
                    slug = summary.project_slug;
                }
            }
        } catch (error) {
            console.warn('Falha ao carregar resumo da cobertura.', error);
        }
    }
    if (!slug && summary?.project_slug) {
        slug = summary.project_slug;
    }

    const heatmapResponse = await fetch(
        `/projects/${encodeURIComponent(slug)}/assets/${encodeURIComponent(lastCoverage.asset_id)}/preview`
    );
    if (!heatmapResponse.ok) {
        throw new Error('Não foi possível carregar a imagem da cobertura.');
    }
    const heatmapBase64 = await blobToBase64(await heatmapResponse.blob());

    let colorbarBase64 = null;
    if (lastCoverage.colorbar_asset_id) {
        try {
            const colorbarResponse = await fetch(
                `/projects/${encodeURIComponent(slug)}/assets/${encodeURIComponent(lastCoverage.colorbar_asset_id)}/preview`
            );
            if (colorbarResponse.ok) {
                colorbarBase64 = await blobToBase64(await colorbarResponse.blob());
            }
        } catch (error) {
            console.warn('Falha ao carregar barra de cores da cobertura.', error);
        }
    }

    const coveragePayload = {
        images: {
            dbuv: {
                image: heatmapBase64,
                colorbar: colorbarBase64,
                label: summary?.scale?.label || 'Campo elétrico [dBµV/m]',
                unit: 'dBµV/m',
            },
        },
        bounds: summary?.bounds || lastCoverage.bounds,
        colorbar_bounds: summary?.colorbar_bounds || lastCoverage.colorbar_bounds,
        scale: summary?.scale || lastCoverage.scale,
        center: summary?.center || lastCoverage.center,
        requested_radius_km: summary?.requested_radius_km
            || lastCoverage.requested_radius_km
            || lastCoverage.radius_km,
        radius: lastCoverage.radius_km,
        gain_components: summary?.gain_components || lastCoverage.gain_components,
        loss_components: summary?.loss_components || lastCoverage.loss_components,
        center_metrics: summary?.center_metrics || lastCoverage.center_metrics,
        signal_level_dict: summary?.signal_level_dict,
        signal_level_dict_dbm: summary?.signal_level_dict_dbm,
        location_status: summary?.location_status || lastCoverage.location_status,
        receivers: lastCoverage.receivers || summary?.receivers || [],
        rt3dScene: summary?.rt3d_scene || lastCoverage.rt3d_scene || null,
        rt3dDiagnostics: summary?.rt3d_diagnostics || lastCoverage.rt3d_diagnostics || null,
        rt3dRays: summary?.rt3d_rays || lastCoverage.rt3d_rays || null,
        rt3dSettings: summary?.rt3d_settings || lastCoverage.rt3d_settings || null,
        tiles: _sanitizeTileConfig(summary?.tiles) ?? _sanitizeTileConfig(lastCoverage.tiles) ?? null,
    };

    const centerLat = parseNullableNumber(
        coveragePayload.center?.lat ?? coveragePayload.center?.latitude
    );
    const centerLng = parseNullableNumber(
        coveragePayload.center?.lng ?? coveragePayload.center?.longitude
    );
    if (Number.isFinite(centerLat) && Number.isFinite(centerLng)) {
        coveragePayload.center = { lat: centerLat, lng: centerLng };
    } else {
        coveragePayload.center = null;
    }

    const requestedRadius = parseNullableNumber(coveragePayload.requested_radius_km);
    const radiusKm = parseNullableNumber(coveragePayload.radius ?? lastCoverage.radius_km);
    coveragePayload.requested_radius_km = requestedRadius ?? radiusKm ?? null;
    coveragePayload.radius = radiusKm ?? requestedRadius ?? null;

    state.coverageData = {
        ...(state.coverageData || {}),
        ...lastCoverage,
        ...coveragePayload,
        summary,
    };
    state.coverageData.asset_id = lastCoverage.asset_id;
    state.coverageData.json_asset_id = lastCoverage.json_asset_id;
    state.coverageData.colorbar_asset_id = lastCoverage.colorbar_asset_id;
    state.coverageData.engine = lastCoverage.engine || state.coverageData.engine || 'p1546';
    state.coverageData.receivers = coveragePayload.receivers;
    if (state.savedReceiverBookmarks.length) {
        state.coverageData.receivers = mergeReceiverSnapshots(
            state.savedReceiverBookmarks,
            state.coverageData.receivers || [],
        );
    }
    state.coverageData.signal_level_dict = coveragePayload.signal_level_dict;
    state.coverageData.signal_level_dict_dbm = coveragePayload.signal_level_dict_dbm;
    state.coverageData.tiles = _sanitizeTileConfig(coveragePayload.tiles) || null;
    state.coverageData.project_slug = slug;
    state.coverageData.rt3dScene = coveragePayload.rt3dScene || summary?.rt3d_scene || lastCoverage.rt3d_scene || state.coverageData.rt3dScene || null;
    state.coverageData.rt3dDiagnostics = coveragePayload.rt3dDiagnostics || summary?.rt3d_diagnostics || lastCoverage.rt3d_diagnostics || state.coverageData.rt3dDiagnostics || null;
    state.coverageData.rt3dRays = coveragePayload.rt3dRays || summary?.rt3d_rays || lastCoverage.rt3d_rays || state.coverageData.rt3dRays || null;
    state.coverageData.rt3dSettings = coveragePayload.rt3dSettings || summary?.rt3d_settings || lastCoverage.rt3d_settings || state.coverageData.rt3dSettings || null;
    if (state.coverageData.engine !== 'rt3d') {
        state.coverageData.rt3dScene = null;
        state.coverageData.rt3dDiagnostics = null;
        state.coverageData.rt3dRays = null;
        state.coverageData.rt3dSettings = null;
        state.isRt3dLayerVisible = true;
        state.isRt3dRaysVisible = true;
    }
    if (state.txData) {
        state.txData.location_status = coveragePayload.location_status || state.txData.location_status;
        if (coveragePayload.center) {
            state.txData.latitude = coveragePayload.center.lat;
            state.txData.longitude = coveragePayload.center.lng;
        }
        if (coveragePayload.tx_location_name) {
            state.txData.txLocationName = coveragePayload.tx_location_name;
        }
        if (coveragePayload.tx_site_elevation !== undefined && coveragePayload.tx_site_elevation !== null) {
            state.txData.txElevation = coveragePayload.tx_site_elevation;
        }
        if (coveragePayload.tx_parameters) {
            const params = coveragePayload.tx_parameters;
            if (params.power_w !== undefined) state.txData.transmissionPower = params.power_w;
            if (params.tower_height_m !== undefined) state.txData.towerHeight = params.tower_height_m;
            if (params.rx_height_m !== undefined) state.txData.rxHeight = params.rx_height_m;
            if (params.total_loss_db !== undefined) state.txData.total_loss = params.total_loss_db;
            if (params.antenna_gain_dbi !== undefined) state.txData.antennaGain = params.antenna_gain_dbi;
        }
        updateTxSummary(state.txData);
    }

    if (coveragePayload.center) {
        const centerLatLng = new google.maps.LatLng(coveragePayload.center.lat, coveragePayload.center.lng);
        setTxCoords(centerLatLng, { pan: false });
    }

    if (coveragePayload.requested_radius_km) {
        const radiusInput = document.getElementById('radiusInput');
        if (radiusInput) {
            const radiusValue = Number(coveragePayload.requested_radius_km);
            if (Number.isFinite(radiusValue)) {
                radiusInput.value = Math.max(radiusValue, Number(radiusInput.min) || 0);
            }
            updateRadiusLabel();
        }
    }

    applyCoverageOverlay(coveragePayload);
    renderRt3dScene(state.coverageData.rt3dScene);
    updateGainSummary(coveragePayload.gain_components, coveragePayload.scale);
    updateLossSummary(coveragePayload.loss_components);
    updateCenterSummary(coveragePayload.center_metrics);

    if (Array.isArray(coveragePayload.receivers)) {
        const mergedReceivers = state.coverageData.receivers || coveragePayload.receivers;
        restoreReceivers(mergedReceivers);
        refreshReceiverSummaries();
        state.coverageData.receivers = serializeReceivers();
    }

    showToast('Última cobertura restaurada.');
    return coveragePayload;
}

function setOverlayOpacity(value) {
    state.overlayOpacity = value;
    if (state.coverageOverlay) {
        state.coverageOverlay.setOpacity(value);
    }
    if (state.tileOverlayLayer) {
        if (typeof state.tileOverlayLayer.setOpacity === 'function') {
            state.tileOverlayLayer.setOpacity(value);
        } else {
            state.tileOverlayLayer.set('opacity', value);
        }
    }
    if (state.radiusCircle) {
        state.radiusCircle.setOptions({ fillOpacity: Math.max(0.05, value / 6) });
    }
}

function findNearestFieldStrength(lat, lng, dict) {
    let best = null;
    let bestDist = Infinity;
    Object.entries(dict).forEach(([key, value]) => {
        const [lt, ln] = key.slice(1, -1).split(',').map(Number);
        const dist = Math.hypot(lat - lt, lng - ln);
        if (dist < bestDist) {
            bestDist = dist;
            best = value;
        }
    });
    return best;
}

function updateLinkSummary(summary) {
    document.getElementById('linkDistance').textContent = summary.distance || '-';
    document.getElementById('linkBearing').textContent = summary.bearing || '-';
    document.getElementById('linkField').textContent = summary.field || '-';
    document.getElementById('linkElevation').textContent = summary.elevation || '-';
    const populationLabel = document.getElementById('linkPopulation');
    if (populationLabel) {
        populationLabel.textContent = summary.population || '-';
    }
}

function highlightRxEntry(index) {
    state.rxEntries.forEach((entry, idx) => {
        entry.marker.setIcon(idx === index ? entry.icons.selected : entry.icons.default);
    });
}

function updateLinkVisuals(entry) {
    if (state.linkLine) {
        state.linkLine.setMap(null);
        state.linkLine = null;
    }
    state.linkLine = new google.maps.Polyline({
        map: state.map,
        path: [state.txCoords, entry.marker.getPosition()],
        strokeColor: '#0d6efd',
        strokeOpacity: 0.9,
        strokeWeight: 3,
    });
    if (entry.summary) {
        updateLinkSummary(entry.summary);
    }
}

function selectRx(index) {
    state.selectedRxIndex = index;
    highlightRxEntry(index);
    const entry = state.rxEntries[index];
    updateLinkVisuals(entry);
    openRxLegend(entry);
    document.getElementById('btnGenerateProfile').disabled = false;
}

function deleteReceiverBookmark(receiverId) {
    const projectSlug = getActiveProjectSlug();
    if (!projectSlug || !receiverId) {
        return Promise.resolve();
    }
    return fetch(`/projects/${encodeURIComponent(projectSlug)}/receivers/${encodeURIComponent(receiverId)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
    }).catch(() => {});
}

function clearProjectReceiversOnServer() {
    const projectSlug = getActiveProjectSlug();
    if (!projectSlug) {
        return Promise.resolve();
    }
    return fetch(`/projects/${encodeURIComponent(projectSlug)}/receivers`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
    }).catch(() => {});
}

function removeRx(index, options = {}) {
    const persist = options.persist !== false;
    const [entry] = state.rxEntries.splice(index, 1);
    if (entry) {
        entry.marker.setMap(null);
        if (entry.id && persist) {
            state.savedReceiverBookmarks = state.savedReceiverBookmarks.filter(
                (bookmark) => bookmark.id !== entry.id,
            );
            deleteReceiverBookmark(entry.id);
        }
    }
    if (state.linkLine) {
        state.linkLine.setMap(null);
        state.linkLine = null;
    }
    state.selectedRxIndex = null;
    updateLinkSummary({});
    renderRxList();
    if (state.coverageData) {
        state.coverageData.receivers = serializeReceivers();
    }
    if (state.rxInfoWindow) {
        state.rxInfoWindow.close();
    }
}

function clearReceivers(options = {}) {
    const persist = options.persist !== false;
    hideRxContextMenu();
    cancelReceiverRelocation();
    while (state.rxEntries.length) {
        removeRx(0, { persist: false });
    }
    state.selectedRxIndex = null;

    if (state.linkLine) {
        state.linkLine.setMap(null);
        state.linkLine = null;
    }

    updateLinkSummary({});
    renderRxList();
    document.getElementById('btnGenerateProfile').disabled = true;
    if (state.rxInfoWindow) {
        state.rxInfoWindow.close();
    }
    if (persist) {
        state.savedReceiverBookmarks = [];
        if (state.coverageData) {
            state.coverageData.receivers = [];
        }
        clearProjectReceiversOnServer()
            .then(() => {
                clearCoverageOverlay();
                state.coverageData = null;
            })
            .catch(() => {
                clearCoverageOverlay();
                state.coverageData = null;
            });
    }
}

function renderRxList() {
    const container = document.getElementById('rxList');
    container.innerHTML = '';

    if (!state.rxEntries.length) {
        container.innerHTML = '<li class="rx-empty">Nenhum ponto RX selecionado.</li>';
        return;
    }

    state.rxEntries.forEach((entry, idx) => {
        const li = document.createElement('li');
        li.className = `rx-item${idx === state.selectedRxIndex ? ' selected' : ''}`;
        const summary = entry.summary || {};

        const header = document.createElement('div');
        header.className = 'rx-header';

        const infoBlock = document.createElement('div');
        infoBlock.className = 'rx-info';

        const title = document.createElement('div');
        title.className = 'rx-title';
        title.textContent = entry.label || getRxLabel(idx);

        const municipality = document.createElement('div');
        municipality.className = 'rx-municipality';
        municipality.textContent = summary.municipality || 'Identificando município...';

        infoBlock.appendChild(title);
        infoBlock.appendChild(municipality);

        const status = document.createElement('div');
        status.className = 'rx-status';
        if (entry.isProfileLoading) {
            status.innerHTML = '<span class="badge text-bg-warning-subtle">Gerando perfil...</span>';
        } else if (entry.profileThumbnail || entry.profileAssetUrl) {
            status.innerHTML = '<span class="badge text-bg-success-subtle">Perfil disponível</span>';
        } else {
            status.innerHTML = '<span class="badge text-bg-secondary-subtle">Pendente</span>';
        }

        header.appendChild(infoBlock);
        header.appendChild(status);

        const preview = document.createElement('div');
        preview.className = 'rx-preview';
        if (entry.profileThumbnail || entry.profileAssetUrl) {
            const img = document.createElement('img');
            img.src = entry.profileThumbnail || entry.profileAssetUrl;
            img.alt = `Perfil ${entry.label}`;
            preview.appendChild(img);
        } else {
            const placeholder = document.createElement('div');
            placeholder.className = 'rx-preview-placeholder';
            placeholder.textContent = 'Prévia indisponível';
            preview.appendChild(placeholder);
        }

        const details = document.createElement('div');
        details.className = 'rx-details';
        details.innerHTML = `
            <span><strong>Distância</strong>${summary.distance || '-'}</span>
            <span><strong>Campo</strong>${summary.field || '-'}</span>
            <span><strong>Altitude</strong>${summary.elevation || '-'}</span>
            <span><strong>Azimute</strong>${summary.bearing || '-'}</span>
            <span><strong>População</strong>${summary.population || '-'}</span>
        `;

        const actions = document.createElement('div');
        actions.className = 'rx-actions';

        const focusBtn = document.createElement('button');
        focusBtn.type = 'button';
        focusBtn.textContent = 'Focar';
        focusBtn.className = 'btn btn-sm btn-outline-primary';
        focusBtn.onclick = (event) => {
            event.stopPropagation();
            hideRxContextMenu();
            state.map.panTo(entry.marker.getPosition());
            state.map.setZoom(Math.max(state.map.getZoom(), 11));
            selectRx(idx);
        };

        const profileBtn = document.createElement('button');
        profileBtn.type = 'button';
        profileBtn.textContent = entry.profileThumbnail || entry.profileAssetUrl ? 'Ver perfil' : 'Gerar perfil';
        profileBtn.className = 'btn btn-sm btn-outline-secondary';
        profileBtn.disabled = entry.isProfileLoading;
        profileBtn.onclick = (event) => {
            event.stopPropagation();
            hideRxContextMenu();
            if (entry.profileThumbnail || entry.profileAssetUrl) {
                showProfileModal(entry);
            } else {
                requestProfileGeneration(entry, { force: true, openModal: true });
            }
        };

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.textContent = 'Remover';
        removeBtn.className = 'btn btn-sm btn-link text-danger';
        removeBtn.onclick = (event) => {
            event.stopPropagation();
            hideRxContextMenu();
            removeRx(idx);
        };

        actions.appendChild(focusBtn);
        actions.appendChild(profileBtn);
        actions.appendChild(removeBtn);

        li.appendChild(header);
        li.appendChild(preview);
        li.appendChild(details);
        li.appendChild(actions);

        li.onclick = () => {
            selectRx(idx);
        };

        container.appendChild(li);
    });

    if (state.coverageData) {
        state.coverageData.receivers = serializeReceivers();
    }
}

function ensureElevationServiceAndGet(position) {
    ensureElevationService();
    return new Promise((resolve) => {
        state.elevationService.getElevationForLocations({ locations: [position] }, (results, status) => {
            if (status === 'OK' && results && results.length) {
                resolve(results[0].elevation);
            } else {
                resolve(null);
            }
        });
    });
}

function computeReceiverSummary(position) {
    const distanceMeters = google.maps.geometry.spherical.computeDistanceBetween(state.txCoords, position);
    const bearingRaw = google.maps.geometry.spherical.computeHeading(state.txCoords, position);
    const distanceKm = distanceMeters / 1000;
    const normalizedBearing = normalizeAzimuth(bearingRaw);

    const summary = {
        distance: `${distanceKm.toFixed(2)} km`,
        distanceValue: Number(distanceKm.toFixed(3)),
        bearing: `${normalizedBearing.toFixed(1)}°`,
        bearingValue: Number(normalizedBearing.toFixed(3)),
        lat: Number(position.lat().toFixed(7)),
        lng: Number(position.lng().toFixed(7)),
    };

    if (state.coverageData && state.coverageData.signal_level_dict) {
        const field = findNearestFieldStrength(
            position.lat(),
            position.lng(),
            state.coverageData.signal_level_dict
        );
        if (field !== null) {
            summary.fieldValue = Number(field.toFixed(3));
            summary.field = `${field.toFixed(1)} dBµV/m`;
        }
    }

    return ensureElevationServiceAndGet(position)
        .then((elevation) => {
            if (elevation !== null) {
                summary.elevationValue = Number(elevation.toFixed(2));
                summary.elevation = `${elevation.toFixed(1)} m`;
            }
            return fetchMunicipality(position);
        })
        .then((municipality) => {
            if (municipality) {
                summary.municipality = municipality.label || municipality;
                summary.population = municipality.population ?? null;
                summary.population_year = municipality.population_year ?? null;
            }
            return summary;
        })
        .catch(() => summary);
}

function createRxMarker(position, options = {}) {
    const {
        selectOnCreate = true,
        presetLabel = null,
        presetSummary = null,
        presetId = null,
        presetProfile = null,
        profileAssetUrl = null,
        profileAssetId = null,
        profileAssetPath = null,
        profileMeta = null,
        profileThumbnail = null,
        autoProfile = true,
    } = options;

    const nextIndex = state.rxEntries.length;
    const labelText = presetLabel || getRxLabel(nextIndex);
    const entryId = presetId || generateReceiverId();

    const defaultIcon = {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: '#6610f2',
        fillOpacity: 0.85,
        scale: 7,
        strokeColor: '#fff',
        strokeWeight: 2,
    };
    const selectedIcon = {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: '#d63384',
        fillOpacity: 1,
        scale: 8,
        strokeColor: '#fff',
        strokeWeight: 2,
    };

    const marker = new google.maps.Marker({
        position,
        map: state.map,
        icon: defaultIcon,
        title: labelText,
        label: {
            text: labelText,
            color: '#ffffff',
            fontWeight: '600',
        },
    });

    const entry = {
        id: entryId,
        marker,
        summary: null,
        icons: { default: defaultIcon, selected: selectedIcon },
        label: labelText,
        profile: presetProfile || null,
        profileAssetUrl: profileAssetUrl || null,
        profileAssetId: profileAssetId || null,
        profileAssetPath: profileAssetPath || null,
        profileMeta: profileMeta || null,
        profileThumbnail: profileThumbnail ? toDataUrl(profileThumbnail) : null,
        isProfileLoading: false,
        profileRequested: Boolean(profileAssetUrl || profileAssetId || presetProfile),
        autoProfileEnabled: autoProfile,
        receiverRecord: options.receiverRecord || null,
    };

    marker.addListener('click', (event) => {
        const index = state.rxEntries.indexOf(entry);
        if (index >= 0) {
            selectRx(index);
        }
        if (event && event.domEvent) {
            event.domEvent.preventDefault();
            event.domEvent.stopPropagation();
        }
        showRxContextMenu(entry, event);
    });

    state.rxEntries.push(entry);

    const startAutoProfile = () => {
        if (!entry.autoProfileEnabled) {
            return;
        }
        if (entry.profileAssetUrl || entry.profileThumbnail || entry.profileRequested) {
            return;
        }
        entry.profileRequested = true;
        requestProfileGeneration(entry, { silent: true }).catch(() => {
            entry.profileRequested = false;
        });
    };

    const hydrateSummary = (summary) => {
        entry.summary = summary;
        if (state.selectedRxIndex === state.rxEntries.indexOf(entry)) {
            updateLinkSummary(summary);
            openRxLegend(entry);
        }
        renderRxList();
        startAutoProfile();
    };

    if (presetSummary) {
        hydrateSummary(presetSummary);
    }

    computeReceiverSummary(position).then(hydrateSummary);

    renderRxList();
    if (selectOnCreate) {
        selectRx(state.rxEntries.length - 1);
    }
    if (state.coverageData) {
        state.coverageData.receivers = serializeReceivers();
    }
}

function handleMapClick(event) {
    hideRxContextMenu();
    if (state.rxMoveCandidate) {
        completeReceiverRelocation(state.rxMoveCandidate, event.latLng);
        return;
    }
    if (!state.txCoords) return;
    const confirmed = window.confirm('Deseja adicionar um receptor neste ponto?');
    if (!confirmed) {
        return;
    }
    createRxMarker(event.latLng);
}

function setTxCoords(latLng, { pan = false } = {}) {
    state.txCoords = latLng;

    if (state.txMarker) {
        state.txMarker.setPosition(latLng);
    }

    if (pan) {
        state.map.panTo(latLng);
    }

    if (state.txData) {
        state.txData.latitude = latLng.lat();
        state.txData.longitude = latLng.lng();
        updateTxSummary(state.txData);
    }

    state.rxEntries.forEach((entry, idx) => {
        if (entry.summary) {
            computeReceiverSummary(entry.marker.getPosition()).then((summary) => {
                entry.summary = summary;
                if (idx === state.selectedRxIndex) {
                    updateLinkSummary(summary);
                }
                renderRxList();
            });
        }
    });

    refreshDirectionGuide();
}

function handleTxDragEnd(event) {
    const position = event.latLng;
    setTxCoords(position, { pan: false });
    persistTxLocation(position).catch(() => {});
}

function saveTilt(value) {
    if (state.pendingTiltTimeout) {
        clearTimeout(state.pendingTiltTimeout);
    }

    state.pendingTiltTimeout = setTimeout(() => {
        fetch('/update-tilt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tilt: value }),
        })
        .then((response) => {
            if (!response.ok) {
                return response.json().catch(() => ({})).then((payload) => {
                    throw new Error(payload.error || 'Falha ao atualizar tilt');
                });
            }
            return response.json();
        })
        .then((payload) => {
            const tiltValue = Number(payload?.antennaTilt ?? value);
            if (state.txData) {
                state.txData.antennaTilt = tiltValue;
            }
            updateTiltLabel(tiltValue);
            showToast('Tilt atualizado');
        })
        .catch((error) => {
            console.error(error);
            showToast('Erro ao atualizar tilt', true);
        });
    }, 350);
}

function saveDirection(value) {
    if (state.pendingDirectionTimeout) {
        clearTimeout(state.pendingDirectionTimeout);
    }

    const normalized = normalizeAzimuth(value);

    state.pendingDirectionTimeout = setTimeout(() => {
        fetch('/update-tilt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ direction: normalized }),
        })
        .then((response) => {
            if (!response.ok) {
                return response.json().catch(() => ({})).then((payload) => {
                    throw new Error(payload.error || 'Falha ao atualizar azimute');
                });
            }
            return response.json();
        })
        .then((payload) => {
            const directionValue = normalizeAzimuth(payload?.antennaDirection ?? normalized);

            if (state.txData) {
                state.txData.antennaDirection = directionValue;
                updateTxSummary(state.txData);
            } else {
                updateDirectionLabel(directionValue);
                const directionDisplay = document.getElementById('txDirection');
                if (directionDisplay) {
                    directionDisplay.textContent = formatAzimuth(directionValue);
                }
            }

            showToast('Azimute atualizado');
            refreshDirectionGuide();
        })
        .catch((error) => {
            console.error(error);
            showToast('Erro ao atualizar azimute', true);
        });
    }, 350);
}

function confirmPersistAndGenerate(options = {}) {
    const { requireConfirm = false } = options;
    const radiusInput = document.getElementById('radiusInput');
    const radiusValue = parseNullableNumber(radiusInput?.value);
    if (!Number.isFinite(radiusValue) || radiusValue <= 0) {
        throw new Error('Informe um raio válido antes de gerar a cobertura.');
    }

    const minField = parseNullableNumber(document.getElementById('minField')?.value);
    const maxField = parseNullableNumber(document.getElementById('maxField')?.value);

    if (!state.txCoords) {
        throw new Error('Posição da TX não definida.');
    }

    if (requireConfirm) {
        const accept = window.confirm('Deseja atualizar a mancha de cobertura usando os parâmetros atuais?');
        if (!accept) {
            return null;
        }
    }

    const coordinates = {
        lat: state.txCoords.lat(),
        lng: state.txCoords.lng(),
    };

    const payload = {
        projectSlug: window.coverageProjectSlug || null,
        coverageEngine: state.coverageData?.engine || state.txData?.coverageEngine || 'p1546',
        radius: radiusValue,
        minSignalLevel: minField,
        maxSignalLevel: maxField,
        customCenter: coordinates,
        receivers: serializeReceivers(),
    };

    const directionControl = document.getElementById('directionControl');
    if (directionControl && directionControl.value !== '') {
        const directionValue = normalizeAzimuth(Number(directionControl.value));
        payload.direction = directionValue;
    }
    const tiltControl = document.getElementById('tiltControl');
    if (tiltControl && tiltControl.value !== '') {
        const tiltValue = Number(tiltControl.value);
        if (Number.isFinite(tiltValue)) {
            payload.tilt = tiltValue;
        }
    }

    return fetch('/calculate-coverage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then((response) => {
            if (!response.ok) {
                return response.json()
                    .catch(() => ({}))
                    .then((payload) => {
                        throw new Error(payload.error || 'Falha ao gerar cobertura');
                    });
            }
            return response.json();
        })
        .then((data) => {
            const coverageState = {
                ...(state.coverageData || {}),
                ...data,
            };

            if (data.assets?.heatmap?.id) {
                coverageState.asset_id = data.assets.heatmap.id;
            }
            if (data.assets?.summary?.id) {
                coverageState.json_asset_id = data.assets.summary.id;
            }
            if (data.assets?.colorbar?.id) {
                coverageState.colorbar_asset_id = data.assets.colorbar.id;
            }
            if (data.lastCoverage) {
                coverageState.asset_id = data.lastCoverage.asset_id || coverageState.asset_id;
                coverageState.json_asset_id = data.lastCoverage.json_asset_id || coverageState.json_asset_id;
                coverageState.colorbar_asset_id = data.lastCoverage.colorbar_asset_id || coverageState.colorbar_asset_id;
                coverageState.radius_km = data.lastCoverage.radius_km ?? coverageState.radius_km;
                coverageState.requested_radius_km = data.lastCoverage.requested_radius_km ?? coverageState.requested_radius_km;
                coverageState.center_metrics = data.lastCoverage.center_metrics || coverageState.center_metrics;
                coverageState.receivers = data.lastCoverage.receivers || coverageState.receivers;
            }

            coverageState.requested_radius_km = data.requested_radius_km ?? coverageState.requested_radius_km ?? payload.radius;
            coverageState.radius_km = data.radius ?? coverageState.radius_km ?? payload.radius;
            coverageState.center_metrics = data.center_metrics || coverageState.center_metrics;
            coverageState.loss_components = data.loss_components || coverageState.loss_components;
            coverageState.gain_components = data.gain_components || coverageState.gain_components;
            coverageState.scale = data.scale || coverageState.scale;
            coverageState.bounds = data.bounds || coverageState.bounds;
            coverageState.location_status = data.location_status || coverageState.location_status;
            coverageState.tiles =
                _sanitizeTileConfig(data.tiles)
                || _sanitizeTileConfig(coverageState.tiles)
                || _sanitizeTileConfig(data.lastCoverage?.tiles)
                || null;
            coverageState.rt3dScene = data.rt3dScene
                || coverageState.rt3dScene
                || data.lastCoverage?.rt3d_scene
                || null;
            coverageState.rt3dDiagnostics = data.rt3dDiagnostics
                || coverageState.rt3dDiagnostics
                || data.lastCoverage?.rt3d_diagnostics
                || null;
            coverageState.rt3dRays = data.rt3dRays
                || coverageState.rt3dRays
                || data.lastCoverage?.rt3d_rays
                || null;

            coverageState.project_slug = data.project_slug || coverageState.project_slug || payload.projectSlug;
            coverageState.generated_at = data.generated_at || coverageState.generated_at;
            coverageState.engine = coverageState.engine || payload.coverageEngine;
            coverageState.receivers = serializeReceivers();
            if (coverageState.engine !== 'rt3d') {
                coverageState.rt3dScene = null;
                coverageState.rt3dDiagnostics = null;
                coverageState.rt3dRays = null;
                state.isRt3dLayerVisible = true;
                state.isRt3dRaysVisible = true;
            }

            state.coverageData = coverageState;
            renderRt3dScene(coverageState.rt3dScene);

            const centerLat = parseNullableNumber(data.center?.lat ?? data.center?.latitude);
            const centerLng = parseNullableNumber(data.center?.lng ?? data.center?.longitude);
            if (Number.isFinite(centerLat) && Number.isFinite(centerLng)) {
                const centerLatLng = new google.maps.LatLng(centerLat, centerLng);
                setTxCoords(centerLatLng, { pan: false });
            }

            if (Number.isFinite(data.requested_radius_km)) {
                const radiusEl = document.getElementById('radiusInput');
                if (radiusEl) {
                    radiusEl.value = Math.max(Number(data.requested_radius_km), Number(radiusEl.min) || 0);
                    updateRadiusLabel();
                }
            }

            if (data.antenna_direction !== undefined && data.antenna_direction !== null) {
                const normalizedDirection = normalizeAzimuth(data.antenna_direction);
                if (state.txData) {
                    state.txData.antennaDirection = normalizedDirection;
                }
                updateDirectionLabel(normalizedDirection);
                const directionDisplay = document.getElementById('txDirection');
                if (directionDisplay) {
                    directionDisplay.textContent = formatAzimuth(normalizedDirection);
                }
                refreshDirectionGuide();
            }

            applyCoverageOverlay(data);
            updateGainSummary(data.gain_components, data.scale);
            updateLossSummary(data.loss_components);
            updateCenterSummary(data.center_metrics);

            state.coverageData.signal_level_dict = data.signal_level_dict;
            state.coverageData.signal_level_dict_dbm = data.signal_level_dict_dbm;

            if (state.txData) {
                if (data.txLocationName || data.tx_location_name) {
                    state.txData.txLocationName = data.txLocationName || data.tx_location_name;
                }
                if (data.txElevation !== undefined || data.tx_site_elevation !== undefined) {
                    state.txData.txElevation = data.txElevation ?? data.tx_site_elevation;
                }
                state.txData.location_status = data.location_status || state.txData.location_status;
                if (payload.direction !== undefined && payload.direction !== null) {
                    state.txData.antennaDirection = payload.direction;
                }
                if (payload.tilt !== undefined && payload.tilt !== null) {
                    state.txData.antennaTilt = payload.tilt;
                }
                updateTxSummary(state.txData);
            }

            refreshReceiverSummaries();
            showToast('Cobertura atualizada com sucesso');
        });
}

function generateCoverage() {
    if (!state.txCoords) {
        showToast('Defina a posição da TX antes de gerar a cobertura', true);
        return;
    }

    setCoverageLoading(true);
    let operation = null;
    try {
        operation = confirmPersistAndGenerate({
            requireConfirm: Boolean(state.coverageData?.asset_id),
        });
    } catch (error) {
        setCoverageLoading(false);
        console.error(error);
        showToast(error.message || 'Não foi possível preparar a geração da cobertura', true);
        return;
    }

    if (!operation) {
        setCoverageLoading(false);
        return;
    }

    operation
        .catch((error) => {
            console.error(error);
            showToast(error.message || 'Não foi possível gerar a cobertura', true);
        })
        .finally(() => {
            setCoverageLoading(false);
        });
}

function showProfileModal(entry) {
    const imageElement = document.getElementById('profileImage');
    if (!imageElement) {
        return;
    }
    updateProfileLegend(entry);
    const source = entry?.profileThumbnail || entry?.profileAssetUrl || null;
    if (!source) {
        showToast('Perfil ainda não disponível para este receptor.', true);
        return;
    }
    imageElement.src = source;
    const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('profileModal'));
    modal.show();
}

function updateProfileLegend(entry) {
    const summary = entry?.summary || {};
    const meta = entry?.profileMeta || {};
    const tx = state.txData || {};
    const setText = (id, value) => {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value ?? '—';
        }
    };

    const formatMeters = (value) => (Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} m` : '—');
    const formatDb = (value, suffix = 'dB') => (Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} ${suffix}` : '—');
    const formatDistance = (value) => (Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)} km` : '—');

    setText('profileTxMunicipality', tx.txLocationName || tx.municipality || '-');
    setText('profileTxAltitude', formatMeters(tx.txElevation));
    setText('profileTxTower', formatMeters(tx.towerHeight));
    const direction = tx.antennaDirection != null ? formatAzimuth(tx.antennaDirection) : null;
    setText('profileTxDirection', direction);

    setText('profileRxMunicipality', summary.municipality || '-');
    setText('profileRxElevation', summary.elevation || formatMeters(summary.elevationValue));
    setText('profileRxField', summary.field || (Number.isFinite(summary.fieldValue) ? `${summary.fieldValue.toFixed(1)} dBµV/m` : '—'));
    const rxHeight = tx.rxHeight != null ? `${Number(tx.rxHeight).toFixed(1)} m` : '—';
    const bearingText = summary.bearing || (Number.isFinite(summary.bearingValue) ? `${summary.bearingValue.toFixed(1)}°` : '—');
    setText('profileRxHeading', `${bearingText} / ${rxHeight}`);

    const metaDistance = meta.distance_km ?? summary.distanceValue;
    setText('profileLinkDistance', formatDistance(metaDistance) || summary.distance || '—');
    setText('profileLinkErp', formatDb(meta.erp_dbm, 'dBm'));
    setText('profileLinkPower', formatDb(meta.rx_power_dbm, 'dBm'));
    setText('profileLinkField', meta.field_dbuv_m != null ? `${Number(meta.field_dbuv_m).toFixed(1)} dBµV/m` : summary.field || '—');
    setText('profileLinkObstacles', meta.obstacles || 'Sem bloqueios relevantes');
}

function requestProfileGeneration(entry, options = {}) {
    const { force = false, openModal = false, silent = false } = options;
    if (!entry || !state.txCoords) {
        if (!silent) {
            showToast('Defina a estação transmissora antes de gerar perfis.', true);
        }
        return Promise.reject(new Error('TX não definido'));
    }
    entry.summary = entry.summary || {};
    if (!force && (entry.profileThumbnail || entry.profileAssetUrl)) {
        if (openModal) {
            showProfileModal(entry);
        }
        return Promise.resolve();
    }
    if (entry.isProfileLoading) {
        return entry.pendingProfilePromise || Promise.resolve();
    }
    const projectSlug = getActiveProjectSlug();
    if (!projectSlug) {
        if (!silent) {
            showToast('Selecione um projeto antes de gerar perfis.', true);
        }
        return Promise.reject(new Error('Projeto não informado'));
    }
    const tx = state.txCoords;
    const rx = entry.marker.getPosition();
    if (!rx) {
        return Promise.reject(new Error('Receptor sem posição definida'));
    }
    const payload = {
        path: [
            { lat: tx.lat(), lng: tx.lng() },
            { lat: rx.lat(), lng: rx.lng() },
        ],
        projectSlug,
        receiverId: entry.id,
        receiverLabel: entry.label,
        summary: summaryPayloadFromEntry(entry),
    };

    entry.isProfileLoading = true;
    renderRxList();

    const promise = fetch('/gerar_img_perfil', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then((response) => {
            if (!response.ok) {
                return response.json().catch(() => ({})).then((json) => {
                    throw new Error(json.error || 'Falha ao gerar perfil');
                });
            }
            return response.json();
        })
        .then((data) => {
            if (data.message && !silent) {
                showToast(data.message, Boolean(data.warning));
            }
            if (data.image) {
                entry.profileThumbnail = toDataUrl(data.image);
            }
            if (data.profile) {
                entry.profile = data.profile;
            }
            if (data.profile_meta) {
                entry.profileMeta = data.profile_meta;
            }
            if (data.asset_id) {
                entry.profileAssetId = data.asset_id;
                entry.profileAssetUrl = data.asset_url || buildAssetPreviewUrl(data.asset_id);
            } else if (!entry.profileAssetUrl && entry.profileAssetId) {
                entry.profileAssetUrl = buildAssetPreviewUrl(entry.profileAssetId);
            }
            if (data.receiver) {
                entry.receiverRecord = data.receiver;
                if (data.receiver.profile_asset_id) {
                    entry.profileAssetId = data.receiver.profile_asset_id;
                    entry.profileAssetUrl = data.receiver.profile_asset_url || entry.profileAssetUrl || buildAssetPreviewUrl(data.receiver.profile_asset_id);
                    entry.profileAssetPath = data.receiver.profile_asset_path || entry.profileAssetPath;
                }
                entry.profileMeta = data.receiver.profile_meta || entry.profileMeta;
                if (!entry.summary?.municipality && data.receiver.municipality) {
                    entry.summary = entry.summary || {};
                    entry.summary.municipality = data.receiver.municipality;
                }
                state.savedReceiverBookmarks = mergeReceiverSnapshots(
                    [data.receiver],
                    state.savedReceiverBookmarks,
                );
            }
            entry.profileRequested = true;
            if (state.coverageData) {
                state.coverageData.receivers = serializeReceivers();
            }
            if (openModal) {
                showProfileModal(entry);
            }
            return data;
        })
        .catch((error) => {
            entry.profileRequested = false;
            if (!silent) {
                showToast(error.message || 'Não foi possível gerar o perfil', true);
            }
            throw error;
        })
        .finally(() => {
            entry.isProfileLoading = false;
            entry.pendingProfilePromise = null;
            renderRxList();
        });

    entry.pendingProfilePromise = promise;
    return promise;
}

function showSelectedProfile() {
    if (state.selectedRxIndex === null) {
        showToast('Selecione um RX na lista', true);
        return;
    }
    const entry = state.rxEntries[state.selectedRxIndex];
    if (!entry) {
        return;
    }
    if (entry.profileThumbnail || entry.profileAssetUrl) {
        showProfileModal(entry);
        return;
    }
    setProfileLoading(true);
    requestProfileGeneration(entry, { force: true, openModal: true })
        .catch(() => {})
        .finally(() => {
            setProfileLoading(false);
        });
}

function initControls() {
    const radiusInput = document.getElementById('radiusInput');
    if (radiusInput) {
        radiusInput.addEventListener('input', updateRadiusLabel);
    }
    document.getElementById('btnGenerateCoverage').addEventListener('click', generateCoverage);
    document.getElementById('btnGenerateProfile').addEventListener('click', showSelectedProfile);
    document.getElementById('btnClearRx').addEventListener('click', clearReceivers);

    const refreshBtn = document.getElementById('refreshMapData');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            synchronizeProjectData(refreshBtn);
        });
    }

    const overlayInput = document.getElementById('overlayOpacity');
    const overlayLabel = document.getElementById('overlayOpacityValue');

    overlayInput.addEventListener('input', (event) => {
        const value = Number(event.target.value);
        overlayLabel.textContent = value.toFixed(2);
        setOverlayOpacity(value);
    });

    overlayInput.value = OVERLAY_DEFAULT_OPACITY;
    overlayLabel.textContent = OVERLAY_DEFAULT_OPACITY.toFixed(2);

    const tiltControl = document.getElementById('tiltControl');
    tiltControl.addEventListener('input', (event) => {
        updateTiltLabel(event.target.value);
    });
    tiltControl.addEventListener('change', (event) => {
        saveTilt(event.target.value);
    });

    const directionControl = document.getElementById('directionControl');
    directionControl.addEventListener('input', (event) => {
        const normalized = normalizeAzimuth(event.target.value);
        event.target.value = normalized;
        updateDirectionLabel(normalized);

        if (state.txData) {
            state.txData.antennaDirection = normalized;
        }

        const directionDisplay = document.getElementById('txDirection');
        if (directionDisplay) {
            directionDisplay.textContent = formatAzimuth(normalized);
        }

        refreshDirectionGuide();
    });
    directionControl.addEventListener('change', (event) => {
        const normalized = normalizeAzimuth(event.target.value);
        event.target.value = normalized;
        saveDirection(normalized);
    });

    updateRadiusLabel();

    const toggleRt3dButton = document.getElementById('toggleRt3dLayer');
    if (toggleRt3dButton) {
        toggleRt3dButton.addEventListener('click', () => {
            if (!state.rt3dLayer.length) {
                return;
            }
            setRt3dLayerVisibility(!state.isRt3dLayerVisible);
        });
    }
    const refreshRt3dButton = document.getElementById('refreshRt3dLayer');
    if (refreshRt3dButton) {
        refreshRt3dButton.addEventListener('click', () => {
            if ((state.coverageData?.engine || state.txData?.coverageEngine) !== 'rt3d') {
                showToast('Gere uma cobertura usando o motor RT3D para atualizar a cena urbana.', true);
                return;
            }
            generateCoverage();
        });
    }
    const toggleRt3dRaysButton = document.getElementById('toggleRt3dRays');
    if (toggleRt3dRaysButton) {
        toggleRt3dRaysButton.addEventListener('click', () => {
            if (!state.rt3dRaysLayer.length) {
                return;
            }
            setRt3dRaysVisibility(!state.isRt3dRaysVisible);
        });
    }
    const openViewerBtn = document.getElementById('openRt3dViewer');
    if (openViewerBtn) {
        openViewerBtn.addEventListener('click', (event) => {
            event.preventDefault();
            const slug = window.coverageProjectSlug;
            if (!slug) {
                showToast('Nenhum projeto selecionado.', true);
                return;
            }
            const url = `/rt3d-viewer?project=${encodeURIComponent(slug)}`;
            window.open(url, '_blank', 'noopener');
        });
    }
    const downloadGeoBtn = document.getElementById('downloadRt3dGeojson');
    if (downloadGeoBtn) {
        downloadGeoBtn.addEventListener('click', (event) => {
            event.preventDefault();
            const slug = window.coverageProjectSlug;
            if (!slug) {
                showToast('Nenhum projeto selecionado.', true);
                return;
            }
            const url = `/projects/${encodeURIComponent(slug)}/rt3d-scene.geojson`;
            window.open(url, '_blank', 'noopener');
        });
    }

    initRxContextMenu();
}

async function synchronizeProjectData(buttonEl) {
    if (!state.map) {
        window.location.reload();
        return;
    }
    hideRxContextMenu();
    cancelReceiverRelocation();
    const slug = getActiveProjectSlug() || window.coverageProjectSlug;
    if (!slug) {
        showToast('Nenhum projeto selecionado.', true);
        return;
    }
    if (buttonEl) {
        buttonEl.disabled = true;
        buttonEl.classList.add('loading');
    }
    try {
        const params = new URLSearchParams({ project: slug, _: Date.now().toString() });
        const response = await fetch(`/carregar-dados?${params.toString()}`);
        if (!response.ok) {
            throw new Error('Falha ao carregar dados do projeto.');
        }
        const data = await response.json();
        const projectSettings = data.projectSettings || {};
        const lastCoverage = projectSettings.lastCoverage || {};
        const container = document.getElementById('coverageMapContainer');
        const { lat: finalLat, lng: finalLng } = resolveProjectCoordinates(
            projectSettings,
            lastCoverage,
            data,
            container,
        );
        const txLatLng = new google.maps.LatLng(finalLat, finalLng);
        setTxCoords(txLatLng, { pan: false });

        state.txData = { ...(state.txData || {}), ...data };
        state.txData.coverageEngine = data.coverageEngine || data.coverage_engine || state.txData.coverageEngine;
        state.txData.txElevation = data.txElevation ?? data.tx_site_elevation ?? state.txData.txElevation;
        state.txData.txLocationName = data.txLocationName || data.tx_location_name || data.municipality || state.txData.txLocationName;
        updateTxSummary(state.txData);
        window.coverageProjectSlug = slug;

        // receptores passam a ser somente os que estão no mapa; bookmarks antigos são substituídos
        const receiverBookmarks = serializeReceivers();
        state.savedReceiverBookmarks = receiverBookmarks;

        state.coverageData = lastCoverage.project_slug || lastCoverage.asset_id
            ? { ...lastCoverage, project_slug: lastCoverage.project_slug || slug }
            : null;
        if (state.coverageData) {
            state.coverageData.tiles = _sanitizeTileConfig(state.coverageData.tiles) || null;
        }

        clearCoverageOverlay();
        clearReceivers({ persist: false });

        if (state.coverageData && state.coverageData.asset_id) {
            try {
                await loadCoverageOverlay(state.coverageData);
            } catch (error) {
                console.warn('Falha ao restaurar cobertura após sincronização.', error);
                if (receiverBookmarks.length) {
                    restoreReceivers(receiverBookmarks);
                } else {
                    renderRxList();
                }
            }
        } else if (state.coverageData && state.coverageData.images) {
            applyCoverageOverlay(state.coverageData);
            const merged = mergeReceiverSnapshots(receiverBookmarks, state.coverageData.receivers || []);
            state.coverageData.receivers = merged;
            restoreReceivers(merged);
        } else if (receiverBookmarks.length) {
            restoreReceivers(receiverBookmarks);
            if (state.coverageData) {
                state.coverageData.receivers = serializeReceivers();
            }
        } else {
            renderRxList();
        }

        // Salvar estado atual dos receptores no backend
        const savePayload = {
            projectSlug: slug,
            receivers: serializeReceivers(),
            txLocationName: state.txData?.txLocationName || null,
            txElevation: state.txData?.txElevation || null,
            latitude: state.txData?.latitude,
            longitude: state.txData?.longitude,
            coverageEngine: state.txData?.coverageEngine,
        };
        await fetch(`/salvar-dados?project=${encodeURIComponent(slug)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(savePayload),
        });

        showToast('Dados salvos com sucesso.');
    } catch (error) {
        console.error('Erro ao sincronizar os dados do projeto', error);
        showToast('Não foi possível sincronizar os dados do projeto.', true);
    } finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.classList.remove('loading');
        }
    }
}

function resolveProjectCoordinates(projectSettings = {}, lastCoverage = {}, payload = {}, container = null) {
    const coverageCenter = lastCoverage.center || lastCoverage.tx_location || null;
    const storedLat = parseNullableNumber(projectSettings.latitude);
    const storedLng = parseNullableNumber(projectSettings.longitude);
    const coverageLat = parseNullableNumber(coverageCenter?.lat ?? coverageCenter?.latitude);
    const coverageLng = parseNullableNumber(coverageCenter?.lng ?? coverageCenter?.longitude);
    const dataLat = parseNullableNumber(payload.latitude);
    const dataLng = parseNullableNumber(payload.longitude);
    const containerLat = container ? parseNullableNumber(container.dataset.startLat) : null;
    const containerLng = container ? parseNullableNumber(container.dataset.startLng) : null;
    return {
        lat: storedLat ?? coverageLat ?? dataLat ?? containerLat ?? 0,
        lng: storedLng ?? coverageLng ?? dataLng ?? containerLng ?? 0,
    };
}

function initCoverageMap() {
    const params = new URLSearchParams();
    const container = document.getElementById('coverageMapContainer');
    if (container && container.dataset.project) {
        params.append('project', container.dataset.project);
        window.coverageProjectSlug = container.dataset.project;
    }

    fetch(`/carregar-dados?${params.toString()}`)
        .then((response) => response.json())
        .then((data) => {
            const projectSettings = data.projectSettings || {};
            const lastCoverage = projectSettings.lastCoverage || {};
            const receiverBookmarks = Array.isArray(data.receiverBookmarks)
                ? data.receiverBookmarks
                : (Array.isArray(projectSettings.receiverBookmarks) ? projectSettings.receiverBookmarks : []);
            state.savedReceiverBookmarks = receiverBookmarks;

            let slugCandidate = data.projectSlug
                || projectSettings.slug
                || lastCoverage.project_slug
                || window.coverageProjectSlug
                || container?.dataset.project
                || null;
            if (slugCandidate) {
                window.coverageProjectSlug = slugCandidate;
            }

            const coverageCenter = lastCoverage.center || lastCoverage.tx_location || null;
            const { lat: finalLat, lng: finalLng } = resolveProjectCoordinates(
                projectSettings,
                lastCoverage,
                data,
                container,
            );

            state.txData = { ...data };
            state.txData.coverageEngine = data.coverageEngine || data.coverage_engine || state.txData.coverageEngine;
            state.txData.txElevation = data.txElevation ?? data.tx_site_elevation ?? state.txData.txElevation;
            state.txData.txLocationName = data.txLocationName || data.tx_location_name || data.municipality || state.txData.txLocationName;
            state.txData.latitude = finalLat;
            state.txData.longitude = finalLng;
            if (lastCoverage.location_status) {
                state.txData.location_status = lastCoverage.location_status;
            }

            state.coverageData = lastCoverage.project_slug || lastCoverage.asset_id
                ? { ...lastCoverage, project_slug: lastCoverage.project_slug || slugCandidate }
                : null;
            if (state.coverageData) {
                state.coverageData.tiles = _sanitizeTileConfig(state.coverageData.tiles) || null;
                state.coverageData.engine = state.coverageData.engine || state.txData.coverageEngine || 'p1546';
                state.coverageData.bounds = state.coverageData.bounds || null;
                state.coverageData.colorbar_bounds = state.coverageData.colorbar_bounds || null;
                state.coverageData.scale = state.coverageData.scale || null;
                state.coverageData.center = state.coverageData.center || coverageCenter || null;
                state.coverageData.requested_radius_km = state.coverageData.requested_radius_km || state.coverageData.radius_km || null;
                state.coverageData.radius = state.coverageData.radius || state.coverageData.radius_km || null;
                state.coverageData.gain_components = state.coverageData.gain_components || null;
                state.coverageData.loss_components = state.coverageData.loss_components || null;
                state.coverageData.center_metrics = state.coverageData.center_metrics || null;
                state.coverageData.signal_level_dict = state.coverageData.signal_level_dict || null;
                state.coverageData.signal_level_dict_dbm = state.coverageData.signal_level_dict_dbm || null;
                state.coverageData.location_status = state.coverageData.location_status || null;
                state.coverageData.rt3dScene = state.coverageData.rt3dScene
                    || state.coverageData.rt3d_scene
                    || null;
                state.coverageData.rt3dDiagnostics = state.coverageData.rt3dDiagnostics
                    || state.coverageData.rt3d_diagnostics
                    || null;
                state.coverageData.rt3dRays = state.coverageData.rt3dRays
                    || state.coverageData.rt3d_rays
                    || null;
                state.coverageData.rt3dSettings = state.coverageData.rt3dSettings
                    || state.coverageData.rt3d_settings
                    || null;
                if (state.coverageData.engine !== 'rt3d') {
                    state.coverageData.rt3dScene = null;
                    state.coverageData.rt3dDiagnostics = null;
                    state.coverageData.rt3dRays = null;
                    state.coverageData.rt3dSettings = null;
                    state.isRt3dLayerVisible = true;
                    state.isRt3dRaysVisible = true;
                }

                if (state.savedReceiverBookmarks.length) {
                    state.coverageData.receivers = mergeReceiverSnapshots(
                        state.savedReceiverBookmarks,
                        state.coverageData.receivers || [],
                    );
                }

                if (state.coverageData.center_metrics) {
                    updateCenterSummary(state.coverageData.center_metrics);
                }
                if (state.coverageData.loss_components) {
                    updateLossSummary(state.coverageData.loss_components);
                }
                const storedRadius = parseNullableNumber(
                    state.coverageData.requested_radius_km ?? state.coverageData.radius_km
                );
                if (Number.isFinite(storedRadius)) {
                    const radiusInput = document.getElementById('radiusInput');
                    if (radiusInput) {
                        radiusInput.value = Math.max(storedRadius, Number(radiusInput.min) || 0);
                        updateRadiusLabel();
                    }
                }
            }

            const txLatLng = new google.maps.LatLng(finalLat, finalLng);
            state.txCoords = txLatLng;

            updateTxSummary(state.txData);

            state.map = new google.maps.Map(document.getElementById('coverageMap'), {
                center: txLatLng,
                zoom: 9,
                mapTypeId: 'terrain',
                gestureHandling: 'greedy',
            });
            state.rxInfoWindow = new google.maps.InfoWindow();
            state.sceneInfoWindow = new google.maps.InfoWindow();

            state.txMarker = new google.maps.Marker({
                position: txLatLng,
                map: state.map,
                title: 'Transmissor',
                draggable: true,
                icon: {
                    url: 'https://maps.gstatic.com/mapfiles/ms2/micons/red-dot.png',
                },
            });

            state.txMarker.addListener('dragend', handleTxDragEnd);

            // Clique direto no mapa (fora do overlay) ainda cria RX
            state.map.addListener('click', handleMapClick);

            initControls();
            refreshDirectionGuide();
            ensureElevationService();

            if (state.coverageData) {
                state.coverageData.signal_level_dict = null;
                state.coverageData.signal_level_dict_dbm = null;
                if (!state.coverageData.rt3dScene && state.coverageData.rt3d_scene) {
                    state.coverageData.rt3dScene = state.coverageData.rt3d_scene;
                }
                renderRt3dScene(state.coverageData.rt3dScene);
            }

            if (state.coverageData && state.coverageData.asset_id) {
                loadCoverageOverlay(state.coverageData)
                    .catch((error) => {
                        console.warn('Não foi possível restaurar a cobertura', error);
                        if (Array.isArray(state.coverageData?.receivers)) {
                            restoreReceivers(state.coverageData.receivers);
                        }
                    });
            } else if (state.coverageData && Array.isArray(state.coverageData.receivers)) {
                restoreReceivers(state.coverageData.receivers);
            } else if (state.savedReceiverBookmarks.length) {
                restoreReceivers(state.savedReceiverBookmarks);
            }
        })
        .catch((error) => {
            console.error('Erro ao carregar dados do usuário', error);
            showToast('Não foi possível carregar os dados iniciais', true);
        });
}

window.initCoverageMap = initCoverageMap;
