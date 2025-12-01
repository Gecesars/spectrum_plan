(function () {
    const config = window.RT3D_VIEWER_CONFIG || {};
    const dataEndpoint = config.dataEndpoint || null;
    const fallbackSceneUrl = config.sceneUrl;
    const token = config.cesiumToken;

    const statusPanel = document.getElementById('rt3dStatusPanel');
    const diagnosticsPanel = document.getElementById('rt3dDiagnosticsPanel');
    const toggleBuildings = document.getElementById('toggleBuildingsCesium');
    const toggleRays = document.getElementById('toggleRaysCesium');

    function setStatus(text) {
        if (statusPanel) {
            statusPanel.textContent = text;
        }
    }

    function renderDiagnostics(diagnostics, settings) {
        if (!diagnosticsPanel) return;
        const parts = [];
        if (diagnostics?.mode) {
            parts.push(`<strong>Modo:</strong> ${diagnostics.mode}`);
        }
        if (diagnostics?.samples) {
            parts.push(`<strong>Amostras:</strong> ${diagnostics.samples}`);
        }
        if (settings?.building_source) {
            parts.push(`<strong>Fonte:</strong> ${settings.building_source}`);
        }
        if (settings?.ray_step_m) {
            parts.push(`<strong>Passo do raio:</strong> ${Number(settings.ray_step_m).toFixed(1)} m`);
        }
        if (!parts.length) {
            diagnosticsPanel.innerHTML = '<p class="text-muted mb-0">Sem diagnósticos disponíveis.</p>';
            return;
        }
        diagnosticsPanel.innerHTML = parts.map((item) => `<p class="mb-1">${item}</p>`).join('');
    }

    function colorFromQuality(dbValue, mode) {
        const q = Number(dbValue);
        if (Number.isFinite(q)) {
            if (q >= -3) return Cesium.Color.fromCssColorString('#22c55e');
            if (q >= -8) return Cesium.Color.fromCssColorString('#facc15');
            return Cesium.Color.fromCssColorString('#f87171');
        }
        const fallback = {
            los: '#22c55e',
            reflection: '#facc15',
            obstruction: '#f87171',
            profile: '#60a5fa',
        };
        return Cesium.Color.fromCssColorString(fallback[mode] || '#f97316');
    }

    async function loadData() {
        if (!dataEndpoint) {
            return {
                scene_url: fallbackSceneUrl,
                rays: [],
                settings: {},
                diagnostics: {},
            };
        }
        const response = await fetch(dataEndpoint);
        if (!response.ok) {
            throw new Error('Falha ao carregar dados RT3D');
        }
        return response.json();
    }

    async function initViewer() {
        try {
            if (token) {
                Cesium.Ion.defaultAccessToken = token;
            }
        } catch (error) {
            console.warn('Não foi possível definir o token do Cesium:', error);
        }

        setStatus('Carregando cena 3D…');
        let payload;
        try {
            payload = await loadData();
        } catch (error) {
            console.error(error);
            setStatus('Não foi possível carregar a cena.');
            return;
        }

        const sceneUrl = payload.scene_url || fallbackSceneUrl;
        if (!sceneUrl) {
            setStatus('Cena não encontrada. Gere uma cobertura RT3D.');
            return;
        }

        const viewer = new Cesium.Viewer('cesiumContainer', {
            animation: false,
            timeline: false,
            baseLayerPicker: true,
            shouldAnimate: false,
        });

        let buildingDataSource = null;
        let rayPrimitives = viewer.scene.primitives.add(new Cesium.PrimitiveCollection());

        Cesium.GeoJsonDataSource.load(sceneUrl, {
            clampToGround: false,
        })
            .then((dataSource) => {
                buildingDataSource = dataSource;
                viewer.dataSources.add(dataSource);
                const entities = dataSource.entities.values;
                entities.forEach((entity) => {
                    const height = (entity.properties && entity.properties.height_m)
                        ? Number(entity.properties.height_m.getValue())
                        : 12;
                    entity.polygon.extrudedHeight = height;
                    entity.polygon.material = Cesium.Color.fromCssColorString('#fb923c').withAlpha(0.75);
                    entity.polygon.outline = true;
                    entity.polygon.outlineColor = Cesium.Color.WHITE.withAlpha(0.35);
                });
                viewer.zoomTo(dataSource);
            })
            .catch((error) => {
                console.error('Falha ao carregar a cena RT3D:', error);
                setStatus('Não foi possível carregar as edificações.');
            });

        function renderRays(rays) {
            rayPrimitives.removeAll();
            if (!Array.isArray(rays) || !rays.length) {
                return;
            }
            const maxRays = 400;
            const sample = rays.slice(0, maxRays);
            sample.forEach((ray) => {
                const path = Array.isArray(ray.path) ? ray.path : [];
                if (path.length < 2) {
                    return;
                }
                const positions = [];
                path.forEach((point, idx, arr) => {
                    const lat = Number(point.lat);
                    const lng = Number(point.lng);
                    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
                        return;
                    }
                    const height = idx === 0 ? 0 : (ray.height_m || 15);
                    positions.push(Cesium.Cartesian3.fromDegrees(lng, lat, height));
                });
                if (positions.length < 2) {
                    return;
                }
                rayPrimitives.add(new Cesium.Primitive({
                    geometryInstances: new Cesium.GeometryInstance({
                        geometry: new Cesium.PolylineGeometry({
                            positions,
                            width: ray.mode === 'reflection' ? 3.0 : 2.0,
                        }),
                        attributes: {
                            color: Cesium.ColorGeometryInstanceAttribute.fromColor(
                                colorFromQuality(ray.quality_db, ray.mode)
                            ),
                        },
                    }),
                    appearance: new Cesium.PolylineColorAppearance(),
                }));
            });
        }

        renderRays(payload.rays);
        renderDiagnostics(payload.diagnostics, payload.settings);
        setStatus('Cena carregada.');

        if (toggleBuildings) {
            toggleBuildings.addEventListener('change', () => {
                if (buildingDataSource) {
                    buildingDataSource.show = toggleBuildings.checked;
                }
            });
        }
        if (toggleRays) {
            toggleRays.addEventListener('change', () => {
                rayPrimitives.show = toggleRays.checked;
            });
        }
    }

    document.addEventListener('DOMContentLoaded', initViewer);
})();
