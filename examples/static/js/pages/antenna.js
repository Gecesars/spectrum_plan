(function () {
    let fileContentGlobal = null;

    const fileInput = document.getElementById('fileInput');
    const directionInput = document.getElementById('directionInput');
    const tiltInput = document.getElementById('tiltInput');
    const fileNameLabel = document.getElementById('fileName');
    const horizontalContainer = document.getElementById('horizontalChart');
    const verticalContainer = document.getElementById('verticalChart');

    function renderPlaceholder(container, message) {
        container.innerHTML = `<div class="placeholder">${message}</div>`;
    }

    function renderCharts(data) {
        const { horizontal_image_base64: horizontalBase64, vertical_image_base64: verticalBase64 } = data;

        if (horizontalBase64) {
            const img = new Image();
            img.src = `data:image/png;base64,${horizontalBase64}`;
            img.alt = 'Diagrama de radiação horizontal';
            horizontalContainer.innerHTML = '';
            horizontalContainer.appendChild(img);
        } else {
            renderPlaceholder(horizontalContainer, 'Nenhum diagrama horizontal disponível.');
        }

        if (verticalBase64) {
            const img = new Image();
            img.src = `data:image/png;base64,${verticalBase64}`;
            img.alt = 'Diagrama de radiação vertical';
            verticalContainer.innerHTML = '';
            verticalContainer.appendChild(img);
        } else {
            renderPlaceholder(verticalContainer, 'Nenhum diagrama vertical disponível.');
        }
    }

    function getProjectSlug() {
        // Tenta pegar do URL query param 'project'
        const params = new URLSearchParams(window.location.search);
        return params.get('project') || '';
    }

    async function loadExistingDiagrams() {
        try {
            const project = getProjectSlug();
            const url = project ? `/carregar_imgs?project=${encodeURIComponent(project)}` : '/carregar_imgs';
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error('Não foi possível carregar os diagramas salvos.');
            }
            const data = await response.json();
            if (data.fileContent) {
                fileContentGlobal = data.fileContent;
                const firstLine = data.fileContent.split('\n')[0] || 'Diagrama carregado';
                fileNameLabel.textContent = firstLine.trim();
            }
            renderCharts(data);
        } catch (error) {
            renderPlaceholder(horizontalContainer, 'Erro ao carregar diagrama.');
            renderPlaceholder(verticalContainer, 'Erro ao carregar diagrama.');
            console.error(error);
        }
    }

    function buildFormData(includeFile = true) {
        const formData = new FormData();
        const direction = directionInput.value.trim();
        const tilt = tiltInput.value.trim();

        if (includeFile) {
            const file = fileInput.files[0];
            if (file) {
                formData.append('file', file, file.name);
            } else if (fileContentGlobal) {
                const blob = new Blob([fileContentGlobal], { type: 'text/plain' });
                formData.append('file', blob, 'current_diagram.pat');
            } else {
                throw new Error('Carregue um arquivo .pat para continuar.');
            }
        }

        formData.append('direction', direction || '');
        formData.append('tilt', tilt || '');

        const project = getProjectSlug();
        if (project) {
            formData.append('project', project);
        }

        return formData;
    }

    async function updateDiagram() {
        try {
            const response = await fetch('/upload_diagrama', {
                method: 'POST',
                body: buildFormData(true),
            });
            const data = await response.json();
            if (data.error) {
                throw new Error(data.error);
            }
            renderCharts(data);
            showToast('Ajustes aplicados com sucesso.', 'success');
        } catch (error) {
            console.error(error);
            showToast(error.message || 'Falha ao aplicar ajustes.', 'danger');
        }
    }

    async function saveDiagram() {
        try {
            const response = await fetch('/salvar_diagrama', {
                method: 'POST',
                body: buildFormData(true),
            });
            const data = await response.json();
            if (data.error) {
                throw new Error(data.error);
            }
            showToast('Diagrama salvo com sucesso.', 'success');
        } catch (error) {
            console.error(error);
            showToast(error.message || 'Erro ao salvar diagrama.', 'danger');
        }
    }

    function handleFileSelection(event) {
        const file = event.target.files[0];
        if (!file) {
            return;
        }
        fileNameLabel.textContent = file.name;
        updateDiagram();
    }

    function showToast(message, variant = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-bg-${variant} border-0`;
        toast.role = 'alert';
        toast.innerHTML = `<div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>`;
        document.body.appendChild(toast);
        if (window.bootstrap && bootstrap.Toast) {
            const bsToast = bootstrap.Toast.getOrCreateInstance(toast, { delay: 2600 });
            bsToast.show();
            toast.addEventListener('hidden.bs.toast', () => toast.remove());
        } else {
            alert(message);
            toast.remove();
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        loadExistingDiagrams();

        document.getElementById('uploadTriggerBtn')?.addEventListener('click', () => fileInput.click());
        fileInput?.addEventListener('change', handleFileSelection);
        document.getElementById('applySettingsBtn')?.addEventListener('click', updateDiagram);
        document.getElementById('saveDiagramBtn')?.addEventListener('click', saveDiagram);
        document.getElementById('goCoverageBtn')?.addEventListener('click', () => {
            window.location.href = '/calcular-cobertura';
        });
        document.getElementById('backToDashboardBtn')?.addEventListener('click', () => {
            window.location.href = '/home';
        });
    });

    // compatibilidade com chamadas existentes
    window.salvarDiagrama = saveDiagram;
    window.sendDirectionAndFile = updateDiagram;
    window.applyTilt = updateDiagram;
    window.loadExistingDiagrams = loadExistingDiagrams;
})();
