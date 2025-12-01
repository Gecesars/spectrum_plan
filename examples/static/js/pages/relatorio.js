(function () {
    const app = document.getElementById('reportEditorApp');
    if (!app) return;

    const contextUrl = app.dataset.contextUrl;
    const projectSlug = app.dataset.project;
    const spinner = document.getElementById('reportLoading');
    const form = document.getElementById('reportForm');
    const coverageImg = document.getElementById('coveragePreviewImg');
    const colorbarImg = document.getElementById('colorbarPreviewImg');
    const diagramHorizontal = document.getElementById('diagramHorizontalImg');
    const diagramVertical = document.getElementById('diagramVerticalImg');
    const profileImg = document.getElementById('profilePreviewImg');
    const receiversTable = document.getElementById('receiverSummaryBody');
    const recommendationsContainer = document.getElementById('recommendationsList');
    const statusBanner = document.getElementById('reportStatusBanner');
    const generateBtn = document.getElementById('generatePdfBtn');
    const linkSummaryBlock = document.getElementById('linkSummaryBlock');
    const logoInput = document.getElementById('companyLogoInput');
    const logoPreview = document.getElementById('companyLogoPreview');
    const logoRemoveBtn = document.getElementById('companyLogoRemoveBtn');
    const validationList = document.getElementById('validationIssues');
    const validateBtn = document.getElementById('validateBtn');

    const fields = {
        headerColor: document.getElementById('inputHeaderColor'),
        projectNotes: document.getElementById('inputProjectNotes'),
        overview: document.getElementById('inputOverview'),
        coverage: document.getElementById('inputCoverage'),
        profile: document.getElementById('inputProfile'),
        patternH: document.getElementById('inputPatternH'),
        patternV: document.getElementById('inputPatternV'),
        conclusion: document.getElementById('inputConclusion'),
    };
    const targetFieldMap = {
        overview: fields.overview,
        coverage: fields.coverage,
        profile: fields.profile,
        pattern_horizontal: fields.patternH,
        pattern_vertical: fields.patternV,
        conclusion: fields.conclusion,
    };
    const fieldLabels = {
        overview: 'Resumo executivo',
        coverage: 'Cobertura',
        profile: 'Perfil do enlace',
        pattern_horizontal: 'Direcionalidade Horizontal',
        pattern_vertical: 'Direcionalidade Vertical',
        conclusion: 'Conclusão',
    };

    const state = {
        aiSections: {},
        recommendations: [],
        headerColor: '#0d47a1',
        projectNotes: '',
        contextLoaded: false,
        companyLogo: null,
        validationIssues: [],
        isValidating: false,
    };

    const notify = (message, variant = 'info') => {
        if (window.showToast) {
            window.showToast(message, variant);
        } else {
            alert(message);
        }
    };

    const renderRecommendations = () => {
        recommendationsContainer.innerHTML = '';
        const recs = state.recommendations.length ? state.recommendations : ['', ''];
        recs.forEach((rec, index) => {
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'form-control';
            input.value = rec || '';
            input.dataset.index = index;
            input.placeholder = `Recomendação ${index + 1}`;
            input.addEventListener('input', (e) => {
                state.recommendations[Number(e.target.dataset.index)] = e.target.value;
            });
            recommendationsContainer.appendChild(input);
        });
    };

    const renderReceiverSummary = (rows = []) => {
        receiversTable.innerHTML = '';
        if (!rows.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 5;
            cell.className = 'text-center text-muted';
            cell.textContent = 'Nenhum receptor qualificado.';
            row.appendChild(cell);
            receiversTable.appendChild(row);
            return;
        }
        rows.forEach((entry) => {
            const row = document.createElement('tr');
            ['label', 'municipality', 'distance_km', 'field_dbuv_m', 'population'].forEach((key) => {
                const cell = document.createElement('td');
                let value = entry[key];
                if (key === 'distance_km' && value != null) value = `${Number(value).toFixed(1)} km`;
                if (key === 'field_dbuv_m' && value != null) value = `${Number(value).toFixed(1)} dBµV/m`;
                if (key === 'population' && value) value = value.toLocaleString('pt-BR');
                cell.textContent = value ?? '—';
                row.appendChild(cell);
            });
            receiversTable.appendChild(row);
        });
    };

    const collectAiSections = () => ({
        overview: fields.overview.value || '',
        coverage: fields.coverage.value || '',
        profile: fields.profile.value || '',
        pattern_horizontal: fields.patternH.value || '',
        pattern_vertical: fields.patternV.value || '',
        conclusion: fields.conclusion.value || '',
        recommendations: state.recommendations.slice(),
    });

    const setValidationLoading = (isLoading) => {
        state.isValidating = isLoading;
        if (validateBtn) {
            validateBtn.disabled = isLoading;
            validateBtn.textContent = isLoading ? 'Verificando...' : 'Verificar inconsistências';
        }
    };

    const applyIssueCorrection = (index) => {
        const issue = state.validationIssues[index];
        if (!issue) return;
        const fieldEl = targetFieldMap[issue.field];
        if (fieldEl && issue.corrected_text) {
            fieldEl.value = issue.corrected_text;
        }
        state.validationIssues.splice(index, 1);
        renderValidationIssues();
        notify('Correção aplicada.', 'success');
    };

    const renderValidationIssues = () => {
        Object.values(targetFieldMap).forEach((input) => {
            if (input) input.classList.remove('field-invalid');
        });
        if (!validationList) return;
        validationList.innerHTML = '';
        if (!state.validationIssues.length) {
            validationList.innerHTML = '<p class="text-muted mb-0 small">Nenhuma verificação realizada.</p>';
            return;
        }
        state.validationIssues.forEach((issue, index) => {
            const fieldKey = issue.field;
            const fieldEl = targetFieldMap[fieldKey];
            if (fieldEl) {
                fieldEl.classList.add('field-invalid');
            }
            const wrapper = document.createElement('div');
            wrapper.className = 'validation-issue';
            const label = fieldLabels[fieldKey] || fieldKey;
            const severity = (issue.severity || 'warning').toLowerCase();
            const badgeClass = severity === 'error' ? 'bg-danger' : 'bg-warning text-dark';
            wrapper.innerHTML = `
                <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
                    <h6 class="mb-0">${label}</h6>
                    <span class="badge ${badgeClass} text-uppercase">${severity}</span>
                </div>
                <p class="mb-1">${issue.issue || 'Inconsistência detectada.'}</p>
                ${issue.evidence ? `<p class="mb-0"><strong>Evidência:</strong> ${issue.evidence}</p>` : ''}
            `;
            if (issue.corrected_text) {
                const actions = document.createElement('div');
                actions.className = 'issue-actions';
                const applyBtn = document.createElement('button');
                applyBtn.type = 'button';
                applyBtn.className = 'btn btn-sm btn-outline-danger';
                applyBtn.textContent = 'Corrigir';
                applyBtn.addEventListener('click', () => applyIssueCorrection(index));
                actions.appendChild(applyBtn);
                wrapper.appendChild(actions);
            }
            validationList.appendChild(wrapper);
        });
    };

    const runValidation = () => {
        if (!projectSlug) return;
        setValidationLoading(true);
        const payload = {
            project: projectSlug,
            ai_sections: collectAiSections(),
        };
        fetch('/api/reports/analysis/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
        })
            .then((response) => {
                if (!response.ok) {
                    return response.json().catch(() => ({})).then((json) => {
                        throw new Error(json.error || 'Não foi possível verificar as inconsistências.');
                    });
                }
                return response.json();
            })
            .then((data) => {
                state.validationIssues = Array.isArray(data.issues) ? data.issues : [];
                if (!state.validationIssues.length) {
                    notify('Nenhuma inconsistência encontrada.');
                }
                renderValidationIssues();
            })
            .catch((error) => {
                console.error(error);
                notify(error.message || 'Falha ao verificar inconsistências.', 'danger');
            })
            .finally(() => setValidationLoading(false));
    };

    const applyContext = (data) => {
        state.contextLoaded = true;
        state.headerColor = data.header_color || '#0d47a1';
        state.projectNotes = data.notes || '';
        state.aiSections = data.ai_sections || {};
        state.recommendations = Array.isArray(state.aiSections.recommendations)
            ? state.aiSections.recommendations.slice()
            : [];
        state.companyLogo = data.branding?.company_logo || null;
        state.validationIssues = [];

        fields.headerColor.value = state.headerColor;
        fields.projectNotes.value = state.projectNotes;
        fields.overview.value = state.aiSections.overview || '';
        fields.coverage.value = state.aiSections.coverage || '';
        fields.profile.value = state.aiSections.profile || '';
        fields.patternH.value = state.aiSections.pattern_horizontal || '';
        fields.patternV.value = state.aiSections.pattern_vertical || '';
        fields.conclusion.value = state.aiSections.conclusion || '';

        const coveragePreviewSrc = data.coverage?.map_snapshot_url || data.coverage?.heatmap_url;
        if (coverageImg && coveragePreviewSrc) {
            coverageImg.src = coveragePreviewSrc;
        }
        if (colorbarImg && data.coverage?.colorbar_url) {
            colorbarImg.src = data.coverage.colorbar_url;
            colorbarImg.classList.remove('d-none');
        }
        if (diagramHorizontal && data.diagram_images?.diagrama_horizontal) {
            diagramHorizontal.src = data.diagram_images.diagrama_horizontal;
        }
        if (diagramVertical && data.diagram_images?.diagrama_vertical) {
            diagramVertical.src = data.diagram_images.diagrama_vertical;
        }
        if (profileImg && data.diagram_images?.perfil) {
            profileImg.src = data.diagram_images.perfil;
        }
        if (logoPreview) {
            if (state.companyLogo) {
                logoPreview.src = state.companyLogo;
                logoPreview.classList.remove('d-none');
            } else {
                logoPreview.src = '';
                logoPreview.classList.add('d-none');
            }
        }

        renderRecommendations();
        renderReceiverSummary(data.receiver_summary);
        if (linkSummaryBlock) {
            linkSummaryBlock.textContent = data.link_summary || 'Nenhum receptor cadastrado.';
        }
        renderValidationIssues();
        if (statusBanner) statusBanner.classList.add('d-none');
        app.classList.remove('d-none');
    };

    const uploadCompanyLogo = async (dataUrl) => {
        try {
            const response = await fetch('/api/reports/logo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ project: projectSlug, logo: dataUrl }),
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Não foi possível atualizar o logo.');
            }
            state.companyLogo = payload.company_logo || null;
            if (logoPreview) {
                if (state.companyLogo) {
                    logoPreview.src = state.companyLogo;
                    logoPreview.classList.remove('d-none');
                } else {
                    logoPreview.src = '';
                    logoPreview.classList.add('d-none');
                }
            }
            notify('Logo atualizado com sucesso.', 'success');
        } catch (error) {
            notify(error.message || 'Erro ao atualizar o logo.', 'danger');
        }
    };

    const gatherOverrides = () => {
        const overrides = {
            header_color: fields.headerColor.value,
            project_notes: fields.projectNotes.value,
            ai_sections: {
                overview: fields.overview.value,
                coverage: fields.coverage.value,
                profile: fields.profile.value,
                pattern_horizontal: fields.patternH.value,
                pattern_vertical: fields.patternV.value,
                conclusion: fields.conclusion.value,
                recommendations: recommendationsContainer.querySelectorAll('input').length
                    ? Array.from(recommendationsContainer.querySelectorAll('input')).map((input) => input.value).filter(Boolean)
                    : [],
            },
        };
        return overrides;
    };

    const toggleLoading = (isLoading) => {
        if (isLoading) {
            spinner.classList.remove('d-none');
            generateBtn.disabled = true;
        } else {
            spinner.classList.add('d-none');
            generateBtn.disabled = false;
        }
    };

    const loadContext = async () => {
        try {
            const response = await fetch(contextUrl, { credentials: 'same-origin' });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.error || 'Não foi possível carregar o relatório.');
            }
            const data = await response.json();
            applyContext(data);
        } catch (error) {
            if (statusBanner) {
                statusBanner.classList.remove('d-none');
                statusBanner.textContent = error.message || 'Falha ao carregar dados.';
            }
        } finally {
            spinner.classList.add('d-none');
        }
    };

    if (generateBtn) {
        generateBtn.addEventListener('click', async () => {
            if (!state.contextLoaded) {
                notify('Aguarde o carregamento do contexto.', 'warning');
                return;
            }
            const overrides = gatherOverrides();
            toggleLoading(true);
            try {
                const response = await fetch('/api/reports/analysis', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        project: projectSlug,
                        overrides,
                    }),
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Falha ao gerar o relatório.');
                }
                notify('Relatório gerado com sucesso.', 'success');
                if (data.download_url) {
                    window.open(data.download_url, '_blank');
                }
            } catch (error) {
                notify(error.message || 'Erro durante a geração do relatório.', 'danger');
            } finally {
                toggleLoading(false);
            }
        });
    }

    if (logoInput) {
        logoInput.addEventListener('change', (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                if (reader.result) {
                    uploadCompanyLogo(reader.result.toString());
                }
            };
            reader.readAsDataURL(file);
        });
    }

    if (logoRemoveBtn) {
        logoRemoveBtn.addEventListener('click', () => {
            uploadCompanyLogo(null);
        });
    }

    if (validateBtn) {
        validateBtn.addEventListener('click', runValidation);
    }

    loadContext();
})();
