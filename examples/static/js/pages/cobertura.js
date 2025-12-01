(function () {
    const coverageContainer = document.querySelector('.coverage-container');
    const backButton = document.getElementById('backToHomeBtn');

    if (!coverageContainer) {
        if (backButton) {
            backButton.addEventListener('click', () => {
                window.location.href = '/home';
            });
        }
        return;
    }

    const bootstrapRef = window.bootstrap || null;
    const alertsContainer = document.getElementById('coverageAlerts');
    const lastSavedOutput = document.querySelector('[data-role="last-saved"]');

    const ENGINE_INFO = {
        p1546: {
            title: 'Ponto-área · ITU-R P.1546',
            subtitle: 'Broadcast FM/TV, serviços VHF/UHF e estimativas regionais.',
            detail: 'Considera climatologia, clutter urbano/suburbano e percentuais de tempo/local para gerar manchas 2D.'
        },
        itm: {
            title: 'Ponto-a-ponto · ITM / ITU-R P.530',
            subtitle: 'Links LOS/NLOS com análise de perfil e estatística de fading.',
            detail: 'Utiliza modelos determinísticos para trajetórias específicas, levando em conta curvatura, zonas de Fresnel e clima.'
        },
        pycraf: {
            title: 'Pycraf Path Profile',
            subtitle: 'Estudos científicos com SRTM/Topodata e clutter customizado.',
            detail: 'Pipeline integrável para cenários especiais, extraindo métricas detalhadas de atenuação e ganho direcional.'
        },
        rt3d: {
            title: 'Ray Tracing 3D',
            subtitle: 'Ambientes urbanos densos com malha 3D e multipercursos.',
            detail: 'Simulações determinísticas com reflexões, difrações e espalhamento — requer malhas de edifícios e recursos dedicados.'
        }
    };

    const state = {
        projectSlug: coverageContainer.dataset.project || '',
        projectName: coverageContainer.dataset.projectName || '',
        engine: coverageContainer.dataset.selectedEngine || 'p1546',
        txData: {},
    };

    if (!ENGINE_INFO[state.engine]) {
        state.engine = 'p1546';
    }

    toggleEnginePanels(state.engine);

    const modals = {};
    let map;
    let marker;

    function notify(message, variant = 'success', timeout = 4000) {
        if (!alertsContainer) {
            console.log(`[${variant}]`, message);
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = `alert alert-${variant} alert-dismissible fade show`;
        wrapper.setAttribute('role', 'alert');
        wrapper.innerHTML = `
            <span>${message}</span>
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Fechar"></button>
        `;
        alertsContainer.appendChild(wrapper);
        if (timeout) {
            window.setTimeout(() => {
                if (bootstrapRef?.Alert) {
                    bootstrapRef.Alert.getOrCreateInstance(wrapper).close();
                } else if (wrapper.parentElement) {
                    wrapper.parentElement.removeChild(wrapper);
                }
            }, timeout);
        }
    }

    function createFallbackModal(element) {
        return {
            show() {
                element.classList.add('show');
                element.style.display = 'block';
                element.removeAttribute('aria-hidden');
            },
            hide() {
                element.classList.remove('show');
                element.style.display = 'none';
                element.setAttribute('aria-hidden', 'true');
            },
        };
    }

    function initModals() {
        const modalElements = {
            map: document.getElementById('mapModal'),
            coordinates: document.getElementById('coordinatesModal'),
        };

        Object.entries(modalElements).forEach(([key, element]) => {
            if (element) {
                modals[key] = bootstrapRef ? bootstrapRef.Modal.getOrCreateInstance(element) : createFallbackModal(element);
            }
        });

        if (modalElements.map && bootstrapRef) {
            modalElements.map.addEventListener('shown.bs.modal', () => {
                if (map) {
                    google.maps.event.trigger(map, 'resize');
                }
            });
        }
    }

    function toggleEnginePanels(engine) {
        document.querySelectorAll('[data-engine-panel]').forEach((panel) => {
            const dataValue = panel.dataset.enginePanel || '';
            const engines = dataValue.split(',').map((val) => val.trim()).filter(Boolean);
            const shouldShow = engines.length === 0 || engines.includes(engine);
            panel.classList.toggle('d-none', !shouldShow);
            panel.hidden = !shouldShow;
        });
    }

    function setEngine(engine) {
        if (!ENGINE_INFO[engine]) {
            engine = 'p1546';
        }
        state.engine = engine;
        document.querySelectorAll('input[name="coverageEngine"]').forEach((input) => {
            const isActive = input.value === engine;
            input.checked = isActive;
            input.nextElementSibling?.classList.toggle('active', isActive);
        });
        const info = ENGINE_INFO[engine] || ENGINE_INFO.p1546;
        const titleEl = document.getElementById('engineDescriptionTitle');
        const detailEl = document.getElementById('engineDescriptionDetail');
        if (titleEl) {
            titleEl.textContent = info.title;
        }
        if (detailEl) {
            detailEl.textContent = info.detail;
        }
        toggleEnginePanels(engine);
    }

    function updateLastSaved(value) {
        if (!lastSavedOutput) return;
        if (!value) {
            lastSavedOutput.textContent = '—';
            return;
        }
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            lastSavedOutput.textContent = value;
            return;
        }
        lastSavedOutput.textContent = date.toLocaleString();
    }

    const GAIN_OFFSET_DBD = 2.15;

    function parseNumber(value) {
        const number = parseFloat(value);
        return Number.isFinite(number) ? number : null;
    }

    function dbiToDbd(value) {
        const num = Number(value);
        return Number.isFinite(num) ? num - GAIN_OFFSET_DBD : null;
    }

    function dbdToDbi(value) {
        const num = Number(value);
        return Number.isFinite(num) ? num + GAIN_OFFSET_DBD : null;
    }

    function setFieldValue(id, value) {
        const field = document.getElementById(id);
        if (!field) return;
        if (value === null || value === undefined || Number.isNaN(value)) {
            field.value = '';
        } else {
            field.value = value;
        }
    }

    function setSelectValue(id, value) {
        const field = document.getElementById(id);
        if (!field) return;
        if (value === null || value === undefined || value === '') {
            field.value = '';
            return;
        }
        const optionExists = Array.from(field.options).some((opt) => opt.value === value);
        field.value = optionExists ? value : '';
    }

    function replaceMinus(value) {
        return typeof value === 'string' ? value.replace(/[−]/g, '-') : value;
    }

    function setCoordinatesText(lat, lon) {
        const coordinatesField = document.getElementById('coordinates');
        if (!coordinatesField) {
            return;
        }
        if (lat === null || lat === undefined || lon === null || lon === undefined) {
            coordinatesField.value = '';
        } else {
            coordinatesField.value = `Latitude: ${parseFloat(lat).toFixed(6)}, Longitude: ${parseFloat(lon).toFixed(6)}`;
        }
        updateGenerateButton();
    }

    function applyTxMetadataLabels(payload = {}) {
        const nameLabel = document.getElementById('txLocationName');
        if (nameLabel && payload.municipality) {
            nameLabel.textContent = payload.municipality;
        }
        const elevationLabel = document.getElementById('txElevation');
        if (elevationLabel && payload.elevation !== undefined && payload.elevation !== null) {
            elevationLabel.textContent = Number(payload.elevation).toFixed(1);
        }
    }

    function persistTxCoordinates(lat, lon) {
        const parsedLat = Number(lat);
        const parsedLon = Number(lon);
        if (!Number.isFinite(parsedLat) || !Number.isFinite(parsedLon)) {
            return Promise.resolve();
        }
        const payload = {
            latitude: Number(parsedLat.toFixed(6)),
            longitude: Number(parsedLon.toFixed(6)),
        };
        if (state.projectSlug) {
            payload.projectSlug = state.projectSlug;
        }
        return fetch('/tx-location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then((response) => response.json().then((json) => {
                if (!response.ok) {
                    throw new Error(json.error || 'Falha ao salvar coordenadas da TX.');
                }
                return json;
            }))
            .then((data) => {
                applyTxMetadataLabels(data);
                state.txData = state.txData || {};
                state.txData.latitude = payload.latitude;
                state.txData.longitude = payload.longitude;
                if (data.municipality) {
                    state.txData.txLocationName = data.municipality;
                }
                if (data.elevation !== undefined && data.elevation !== null) {
                    state.txData.txElevation = data.elevation;
                }
                return data;
            })
            .catch((error) => {
                console.error(error);
                notify(error.message || 'Não foi possível atualizar os dados da TX.', 'danger');
                throw error;
            });
    }

    function parseCoordinatesFromField() {
        const coordinatesField = document.getElementById('coordinates');
        if (!coordinatesField || !coordinatesField.value) {
            return null;
        }
        const raw = replaceMinus(coordinatesField.value.trim());
        const match = raw.match(/Latitude:\s*([-+]?\d+(?:\.\d+)?),\s*Longitude:\s*([-+]?\d+(?:\.\d+)?)/i);
        if (!match) {
            return null;
        }
        const lat = parseFloat(match[1]);
        const lon = parseFloat(match[2]);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
            return null;
        }
        return { lat, lon };
    }

    function updateGenerateButton() {
        const generateButton = document.getElementById('generateCoverageButton');
        if (!generateButton) return;
        const coords = parseCoordinatesFromField();
        const radiusField = document.getElementById('radiusKm');
        const radiusValue = radiusField ? parseFloat(radiusField.value) : NaN;
        const radiusValid = !Number.isNaN(radiusValue) && radiusValue > 0;
        generateButton.disabled = !(coords && radiusValid);
    }

    async function loadData() {
        if (!state.projectSlug) {
            return;
        }
        try {
            const params = new URLSearchParams({ project: state.projectSlug });
            const response = await fetch(`/carregar-dados?${params.toString()}`);
            if (!response.ok) {
                throw new Error('Não foi possível carregar os parâmetros do projeto.');
            }
            const data = await response.json();
            const projectSettings = data.projectSettings || {};
            state.txData = {
                latitude: data.latitude,
                longitude: data.longitude,
                txLocationName: data.txLocationName,
                txElevation: data.txElevation,
                transmissionPower: data.transmissionPower,
                towerHeight: data.towerHeight,
            };

            setFieldValue('towerHeight', data.towerHeight);
            setFieldValue('rxHeight', data.rxHeight);
            setFieldValue('Total_loss', data.Total_loss);
            setFieldValue('timePercentage', data.timePercentage);
            setSelectValue('polarization', data.polarization ? data.polarization.toLowerCase() : '');
            setSelectValue('p452Version', data.p452Version ? String(data.p452Version) : '');
            setFieldValue('frequency', data.frequency);
            setFieldValue('transmissionPower', data.transmissionPower);
            const displayGain = dbiToDbd(data.antennaGain);
            setFieldValue('antennaGain', displayGain !== null ? displayGain : data.antennaGain);
            setFieldValue('antennaTilt', data.antennaTilt);
            setFieldValue('antennaDirection', data.antennaDirection);
            setFieldValue('temperature', data.temperature);
            setFieldValue('pressure', data.pressure);
            setFieldValue('waterDensity', data.waterDensity);
            setSelectValue('propagationModel', data.propagationModel);
            setSelectValue('serviceType', data.serviceType || data.service);

            const radiusFromServer = data.radius ?? projectSettings.radius ?? (projectSettings.lastCoverage?.radius_km);
            setFieldValue('radiusKm', radiusFromServer ?? '');
            setFieldValue('minSignalLevel', data.minSignalLevel ?? projectSettings.minSignalLevel ?? '');
            setFieldValue('maxSignalLevel', data.maxSignalLevel ?? projectSettings.maxSignalLevel ?? '');

            applyTxMetadataLabels({
                municipality: data.txLocationName || '-',
                elevation: data.txElevation,
            });

            if (data.latitude !== undefined && data.longitude !== undefined && data.latitude !== null && data.longitude !== null) {
                setCoordinatesText(data.latitude, data.longitude);
            } else {
                setCoordinatesText(null, null);
            }

            const engineToApply = data.coverageEngine || state.engine;
            setEngine(engineToApply);

            if (data.projectLastSavedAt) {
                updateLastSaved(data.projectLastSavedAt);
            } else if (projectSettings.lastCoverage && projectSettings.lastCoverage.generated_at) {
                updateLastSaved(projectSettings.lastCoverage.generated_at);
            }
        } catch (error) {
            console.error(error);
            notify(error.message || 'Falha ao carregar parâmetros.', 'danger');
        } finally {
            updateGenerateButton();
        }
    }

    function collectRt3dPayload() {
        const rt3dPayload = {};
        const mapField = (key, elementId) => {
            const value = parseNumber(document.getElementById(elementId)?.value);
            if (value !== null && value !== undefined && !Number.isNaN(value)) {
                rt3dPayload[key] = value;
            }
        };
        mapField('rt3dUrbanRadius', 'rt3dUrbanRadius');
        mapField('rt3dRays', 'rt3dRays');
        mapField('rt3dBounces', 'rt3dBounces');
        mapField('rt3dOcclusionPerMeter', 'rt3dOcclusionPerMeter');
        mapField('rt3dReflectionGain', 'rt3dReflectionGain');
        mapField('rt3dInterferencePenalty', 'rt3dInterferencePenalty');
        mapField('rt3dSamples', 'rt3dSamples');
        mapField('rt3dRings', 'rt3dRings');
        mapField('rt3dBuildingSource', 'rt3dBuildingSource');
        mapField('rt3dRayStep', 'rt3dRayStep');
        mapField('rt3dDiffractionBoost', 'rt3dDiffractionBoost');
        mapField('rt3dMinimumClearance', 'rt3dMinimumClearance');
        const buildingSource = document.getElementById('rt3dBuildingSource')?.value;
        if (buildingSource) {
            rt3dPayload.rt3dBuildingSource = buildingSource;
        }
        return rt3dPayload;
    }

    function collectPayload() {
        const gainInput = parseNumber(document.getElementById('antennaGain')?.value);
        const payload = {
            propagationModel: document.getElementById('propagationModel')?.value || null,
            serviceType: document.getElementById('serviceType')?.value || null,
            txLocationName: document.getElementById('txLocationName')?.value || null,
            Total_loss: parseNumber(document.getElementById('Total_loss')?.value),
            timePercentage: parseNumber(document.getElementById('timePercentage')?.value),
            polarization: document.getElementById('polarization')?.value || null,
            p452Version: document.getElementById('p452Version')?.value || null,
            frequency: parseNumber(document.getElementById('frequency')?.value),
            transmissionPower: parseNumber(document.getElementById('transmissionPower')?.value),
            antennaGain: gainInput !== null ? dbdToDbi(gainInput) : null,
            antennaTilt: parseNumber(document.getElementById('antennaTilt')?.value),
            antennaDirection: parseNumber(document.getElementById('antennaDirection')?.value),
            towerHeight: parseNumber(document.getElementById('towerHeight')?.value),
            rxHeight: parseNumber(document.getElementById('rxHeight')?.value),
            temperature: parseNumber(document.getElementById('temperature')?.value),
            pressure: parseNumber(document.getElementById('pressure')?.value),
            waterDensity: parseNumber(document.getElementById('waterDensity')?.value),
            radius: parseNumber(document.getElementById('radiusKm')?.value),
            minSignalLevel: parseNumber(document.getElementById('minSignalLevel')?.value),
            maxSignalLevel: parseNumber(document.getElementById('maxSignalLevel')?.value),
        };

        const coords = parseCoordinatesFromField();
        if (coords) {
            payload.latitude = coords.lat;
            payload.longitude = coords.lon;
        }

        if (state.engine === 'rt3d' && document.getElementById('rt3dUrbanRadius')) {
            Object.assign(payload, collectRt3dPayload());
        }

        return payload;
    }

    async function persist(mode = 'save') {
        if (!state.projectSlug) {
            notify('Selecione um projeto antes de salvar os parâmetros.', 'warning');
            return;
        }

        const payload = collectPayload();
        payload.coverageEngine = state.engine;
        payload.projectSlug = state.projectSlug;

        const url = `/salvar-dados?project=${encodeURIComponent(state.projectSlug)}`;
        const saveButton = document.getElementById('saveCoverageBtn');
        const generateButton = document.getElementById('generateCoverageButton');

        const targetButton = mode === 'generate' ? generateButton : saveButton;
        const originalLabel = targetButton ? targetButton.innerHTML : '';

        if (targetButton) {
            targetButton.disabled = true;
            targetButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processando...';
        }

        try {
            const response = await axios.post(url, payload);
            const data = response.data || {};
            const isGenerate = mode === 'generate';
            const successMessage = data.message
                || (isGenerate
                    ? 'Parâmetros salvos. Iniciando geração da cobertura...'
                    : 'Parâmetros salvos com sucesso.');
            notify(successMessage, isGenerate ? 'info' : 'success', isGenerate ? 2000 : 4000);

            if (data.projectSettings && data.projectSettings.lastSavedAt) {
                updateLastSaved(data.projectSettings.lastSavedAt);
            }

            if (isGenerate) {
                await runCoverage(payload);
            }
        } catch (error) {
            console.error(error);
            const fallback = mode === 'generate'
                ? 'Falha ao gerar a cobertura.'
                : 'Não foi possível salvar os parâmetros.';
            const message = error?.response?.data?.message
                || error?.response?.data?.detail
                || error?.message
                || fallback;
            notify(message, 'danger');
        } finally {
            if (targetButton) {
                targetButton.disabled = false;
                targetButton.innerHTML = originalLabel;
            }
        }
    }

    async function runCoverage(savedPayload = {}) {
        if (!state.projectSlug) {
            notify('Selecione um projeto antes de gerar a cobertura.', 'warning');
            return;
        }

        const coords = parseCoordinatesFromField();
        if (!coords) {
            notify('Defina as coordenadas da TX antes de gerar a cobertura.', 'warning');
            return;
        }

        const radiusValue = savedPayload.radius ?? parseNumber(document.getElementById('radiusKm')?.value) ?? 20;
        const coveragePayload = {
            projectSlug: state.projectSlug,
            coverageEngine: state.engine,
            radius: radiusValue,
            minSignalLevel: savedPayload.minSignalLevel ?? parseNumber(document.getElementById('minSignalLevel')?.value),
            maxSignalLevel: savedPayload.maxSignalLevel ?? parseNumber(document.getElementById('maxSignalLevel')?.value),
            customCenter: { lat: coords.lat, lng: coords.lon },
        };

        notify('Gerando cobertura...', 'info', 4000);

        if (state.engine === 'rt3d' && document.getElementById('rt3dUrbanRadius')) {
            const rt3dPayload = collectRt3dPayload();
            Object.entries(rt3dPayload).forEach(([key, value]) => {
                const savedValue = savedPayload[key];
                coveragePayload[key] = savedValue ?? value;
            });
        }

        try {
            const response = await fetch('/calculate-coverage', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(coveragePayload),
            });

            if (!response.ok) {
                const errorPayload = await response.json().catch(() => ({}));
                const message = errorPayload?.error || errorPayload?.message || 'Falha ao gerar a cobertura.';
                throw new Error(message);
            }

            const data = await response.json();
            if (data.generated_at) {
                updateLastSaved(data.generated_at);
            }

            if (data.datasetStatus) {
                const { demTile, lulcYear, buildingsSource, buildingsCount } = data.datasetStatus;
                const parts = [];
                if (demTile) {
                    parts.push(`DEM ${demTile}`);
                }
                if (lulcYear) {
                    parts.push(`LULC ${lulcYear}`);
                }
                if (buildingsSource) {
                    const sourceLabel = buildingsSource === 'osm-overpass' ? 'OSM/Overpass' : buildingsSource;
                    const countLabel = buildingsCount ? ` · ${buildingsCount} feições` : '';
                    parts.push(`RT3D ${sourceLabel}${countLabel}`);
                }
                if (parts.length) {
                    notify(`Dados base carregados: ${parts.join(' · ')}`, 'info', 4500);
                }
            }

            notify('Cobertura gerada com sucesso! Atualizando visão do projeto...', 'success');

            window.setTimeout(() => {
                window.location.reload();
            }, 1200);

            return data;
        } catch (error) {
            console.error(error);
            notify(error.message || 'Falha ao gerar a cobertura.', 'danger');
            throw error;
        }
    }

    async function carregarClimaPadrao() {
        const statusEl = document.getElementById('climateStatus');
        const button = document.getElementById('loadClimateBtn');
        if (!statusEl || !button) {
            return;
        }
        statusEl.hidden = false;
        statusEl.textContent = 'Consultando séries históricas (últimos 360 dias)...';
        button.disabled = true;
        try {
            const params = new URLSearchParams();
            if (state.projectSlug) {
                params.set('project', state.projectSlug);
            }

            // Parse current coordinates from the display field
            const coordsField = document.getElementById('coordinates');
            if (coordsField && coordsField.value) {
                const match = coordsField.value.match(/Latitude:\s*([-\d.]+),\s*Longitude:\s*([-\d.]+)/);
                if (match) {
                    params.set('latitude', match[1]);
                    params.set('longitude', match[2]);
                }
            }

            const response = await fetch(`/clima-recomendado${params.toString() ? `?${params.toString()}` : ''}`);
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                const message = errorData.error || 'Não foi possível carregar dados climáticos.';
                throw new Error(message);
            }
            const data = await response.json();
            setFieldValue('temperature', data.temperature);
            setFieldValue('pressure', data.pressure);
            setFieldValue('waterDensity', data.waterDensity);
            statusEl.textContent = `Clima atualizado${data.municipality ? ` para ${data.municipality}` : ''}. Amostra de ${data.daysSampled || 0} dias.`;
        } catch (error) {
            console.error(error);
            statusEl.textContent = error.message || 'Não foi possível carregar dados climáticos.';
        } finally {
            button.disabled = false;
        }
    }

    function placeMarkerAndPanTo(latLng) {
        if (marker) {
            marker.setMap(null);
        }
        marker = new google.maps.Marker({
            position: latLng,
            map,
        });
        map.panTo(latLng);

        const latitude = latLng.lat().toFixed(6);
        const longitude = latLng.lng().toFixed(6);
        setCoordinatesText(latitude, longitude);
    }

    function saveCoordinates() {
        if (!marker) {
            notify('Selecione um ponto no mapa antes de confirmar.', 'warning');
            return;
        }
        const latitude = marker.getPosition().lat().toFixed(6);
        const longitude = marker.getPosition().lng().toFixed(6);
        setCoordinatesText(latitude, longitude);
        persistTxCoordinates(Number(latitude), Number(longitude)).catch(() => { });
        modals.map?.hide();
    }

    function saveManualCoordinates() {
        const latDegrees = parseNumber(document.getElementById('latitudeDegrees')?.value);
        const latMinutes = parseNumber(document.getElementById('latitudeMinutes')?.value) || 0;
        const latSeconds = parseNumber(document.getElementById('latitudeSeconds')?.value) || 0;
        const latDirection = document.getElementById('latitudeDirection')?.value || 'N';

        const lonDegrees = parseNumber(document.getElementById('longitudeDegrees')?.value);
        const lonMinutes = parseNumber(document.getElementById('longitudeMinutes')?.value) || 0;
        const lonSeconds = parseNumber(document.getElementById('longitudeSeconds')?.value) || 0;
        const lonDirection = document.getElementById('longitudeDirection')?.value || 'E';

        if (latDegrees === null || lonDegrees === null) {
            notify('Informe graus para latitude e longitude.', 'warning');
            return;
        }

        const latDecimal = (Math.abs(latDegrees) + (latMinutes / 60) + (latSeconds / 3600)) * (latDirection === 'S' ? -1 : 1);
        const lonDecimal = (Math.abs(lonDegrees) + (lonMinutes / 60) + (lonSeconds / 3600)) * (lonDirection === 'W' ? -1 : 1);

        setCoordinatesText(latDecimal, lonDecimal);
        persistTxCoordinates(latDecimal, lonDecimal).catch(() => { });
        modals.coordinates?.hide();
    }

    function openMapModal() {
        modals.map?.show();
    }

    function openManualCoordinatesModal() {
        modals.coordinates?.show();
    }

    function fecharModalMapa() {
        modals.map?.hide();
    }

    window.initMap = function () {
        const mapCanvas = document.getElementById('map');
        if (!mapCanvas) {
            return;
        }
        map = new google.maps.Map(mapCanvas, {
            center: { lat: -14.235004, lng: -51.92528 },
            zoom: 4,
            gestureHandling: 'greedy',
        });

        map.addListener('click', (event) => {
            placeMarkerAndPanTo(event.latLng);
        });
    };

    function updateDMS(decimalFieldId, degreesFieldId, minutesFieldId, secondsFieldId, directionFieldId) {
        const decimalValue = parseFloat(document.getElementById(decimalFieldId)?.value);
        if (Number.isNaN(decimalValue)) {
            return;
        }
        const sign = Math.sign(decimalValue);
        const absoluteValue = Math.abs(decimalValue);

        let degrees = Math.floor(absoluteValue);
        const fractionalPart = absoluteValue - degrees;
        let minutes = Math.floor(fractionalPart * 60);
        const seconds = Math.round((fractionalPart * 3600) % 60);

        if (minutes === 60) {
            degrees++;
            minutes = 0;
        }

        const degreesField = document.getElementById(degreesFieldId);
        const minutesField = document.getElementById(minutesFieldId);
        const secondsField = document.getElementById(secondsFieldId);
        const directionField = document.getElementById(directionFieldId);

        if (degreesField) degreesField.value = degrees * sign;
        if (minutesField) minutesField.value = minutes;
        if (secondsField) secondsField.value = seconds;
        if (directionField) directionField.value = sign >= 0 ? (directionFieldId.includes('latitude') ? 'N' : 'E') : (directionFieldId.includes('latitude') ? 'S' : 'W');
    }

    function updateDecimal(degreesFieldId, minutesFieldId, secondsFieldId, directionFieldId, decimalFieldId) {
        const degrees = parseFloat(document.getElementById(degreesFieldId)?.value) || 0;
        const minutes = parseFloat(document.getElementById(minutesFieldId)?.value) || 0;
        const seconds = parseFloat(document.getElementById(secondsFieldId)?.value) || 0;
        const directionValue = document.getElementById(directionFieldId)?.value || 'N';
        const direction = (directionValue === 'N' || directionValue === 'E') ? 1 : -1;
        const decimalValue = degrees + (minutes / 60) + (seconds / 3600);
        const decimalField = document.getElementById(decimalFieldId);
        if (decimalField) {
            decimalField.value = (decimalValue * direction).toFixed(6);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        initModals();
        setEngine(state.engine);
        loadData();

        const engineRadios = document.querySelectorAll('input[name="coverageEngine"]');
        engineRadios.forEach((radio) => {
            radio.addEventListener('change', (event) => {
                setEngine(event.target.value);
            });
        });

        const projectSwitcher = document.getElementById('projectSwitcher');
        if (projectSwitcher) {
            projectSwitcher.addEventListener('change', (event) => {
                const slug = event.target.value;
                if (slug) {
                    const params = new URLSearchParams({ project: slug });
                    window.location.href = `/calcular-cobertura?${params.toString()}`;
                }
            });
        }

        const askCoordinatesBtn = document.getElementById('askCoordinatesBtn');
        if (askCoordinatesBtn) {
            askCoordinatesBtn.addEventListener('click', openMapModal);
        }

        const openManualCoordinatesBtn = document.getElementById('openManualCoordinatesBtn');
        if (openManualCoordinatesBtn) {
            openManualCoordinatesBtn.addEventListener('click', openManualCoordinatesModal);
        }

        const saveMapPointBtn = document.getElementById('saveCoordinatesBtn');
        if (saveMapPointBtn) {
            saveMapPointBtn.addEventListener('click', saveCoordinates);
        }

        const saveManualCoordinatesBtn = document.getElementById('saveManualCoordinatesBtn');
        if (saveManualCoordinatesBtn) {
            saveManualCoordinatesBtn.addEventListener('click', saveManualCoordinates);
        }

        const closeMapModalBtn = document.getElementById('closeMapModalBtn');
        if (closeMapModalBtn) {
            closeMapModalBtn.addEventListener('click', fecharModalMapa);
        }

        const saveFormBtn = document.getElementById('saveCoverageBtn');
        if (saveFormBtn) {
            saveFormBtn.addEventListener('click', () => persist('save'));
        }

        const generateCoverageBtn = document.getElementById('generateCoverageButton');
        if (generateCoverageBtn) {
            generateCoverageBtn.addEventListener('click', () => persist('generate'));
        }

        const refreshDataBtn = document.getElementById('refreshDataBtn');
        if (refreshDataBtn) {
            refreshDataBtn.addEventListener('click', loadData);
        }

        const loadClimateBtn = document.getElementById('loadClimateBtn');
        if (loadClimateBtn) {
            loadClimateBtn.addEventListener('click', carregarClimaPadrao);
        }

        const openCoverageMapBtn = document.getElementById('openCoverageMapBtn');
        if (openCoverageMapBtn) {
            openCoverageMapBtn.addEventListener('click', (event) => {
                event.preventDefault();
                if (!state.projectSlug) {
                    notify('Selecione um projeto antes de abrir o mapa.', 'warning');
                    return;
                }
                const baseUrl = openCoverageMapBtn.dataset.mapUrl || '/mapa';
                const params = new URLSearchParams({ project: state.projectSlug });
                window.location.href = `${baseUrl}?${params.toString()}`;
            });
        }

        if (backButton) {
            backButton.addEventListener('click', () => {
                window.location.href = '/home';
            });
        }

        const radiusField = document.getElementById('radiusKm');
        if (radiusField) {
            radiusField.addEventListener('input', updateGenerateButton);
        }

        updateGenerateButton();
    });

    window.coverageForm = {
        updateDMS,
        updateDecimal,
    };

    window.placeMarkerAndPanTo = placeMarkerAndPanTo;
    window.saveCoordinates = saveCoordinates;
    window.saveManualCoordinates = saveManualCoordinates;
    window.askForCoordinates = openMapModal;
    window.fecharModalMapa = fecharModalMapa;
})();
