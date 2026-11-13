// Presentation only: Authentik retains its native forms, source callbacks and flow state.
const shell = document.getElementById('pp-login');
if (shell) {
    const executor = shell.querySelector('ak-flow-executor');
    const google = document.getElementById('pp-google');
    const admin = document.getElementById('pp-admin');
    const intro = document.getElementById('pp-introduction');
    const title = document.getElementById('pp-title');
    const description = document.getElementById('pp-description');
    const access = document.getElementById('pp-access');
    let local = new URL(location.href).searchParams.get('pp-local') === '1';
    let nativeGoogle;
    let identification;

    function sync() {
        identification = executor.shadowRoot?.querySelector('ak-stage-identification');
        const sources = executor.challenge?.sources || [];
        const index = sources.findIndex(source => source.name === 'Google');
        const buttons = identification?.shadowRoot?.querySelectorAll('.pf-c-login__main-footer-links-item button');
        nativeGoogle = index >= 0 ? buttons?.[index] : undefined;
        // Never hide the native form unless its working Google control was found.
        // Unknown stages, missing Google, and errors always retain the native UI.
        const errors = executor.challenge?.responseErrors;
        const hasErrors = errors && Object.values(errors).some(value => value?.length);
        const ready = Boolean(identification && nativeGoogle && !hasErrors);
        executor.hidden = ready && !local;
        executor.toggleAttribute('data-pp-identification', Boolean(identification));
        executor.style.setProperty('--pp-native-title', ready ? 'none' : 'block');
        intro.hidden = !ready;
        google.hidden = !ready || local;
        google.disabled = Boolean(executor.loading);
        google.setAttribute('aria-busy', String(Boolean(executor.loading)));
        admin.hidden = !ready;
        access.hidden = !ready;
        title.textContent = local ? 'Administrator sign-in' : 'Sign in';
        description.textContent = local
            ? 'Use your local account for administration and recovery.'
            : 'Welcome back. Use your approved Google account to access your workspace.';
        access.textContent = local
            ? 'Account permissions still determine access.'
            : 'Access is limited to approved team members.';
        admin.textContent = local ? 'Back to Google sign-in' : 'Administrator sign-in';
        const destination = new URL(location.href);
        if (local) destination.searchParams.delete('pp-local');
        else destination.searchParams.set('pp-local', '1');
        admin.href = destination.pathname + destination.search + destination.hash;
    }

    google.addEventListener('click', () => {
        // Use the original control and its bound challenge; never construct OAuth URLs.
        if (nativeGoogle && !executor.loading) {
            google.disabled = true;
            nativeGoogle.click();
        }
    });
    admin.addEventListener('click', event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        local = !local;
        history.replaceState(history.state, '', admin.href);
        sync();
        title.focus();
    });
    window.addEventListener('popstate', () => {
        local = new URL(location.href).searchParams.get('pp-local') === '1';
        sync();
    });

    function fallback(error) {
        executor.hidden = false;
        intro.hidden = google.hidden = admin.hidden = access.hidden = true;
        console.error('Unable to enhance the login presentation.', error);
    }

    try {
        await customElements.whenDefined('ak-flow-executor');
        await executor.updateComplete;
        executor.setAttribute('data-pp-branded', '');
        const observedStages = new WeakSet();
        const observer = new MutationObserver(() => {
            sync();
            observeStage();
        });
        function observeStage() {
            const stage = executor.shadowRoot?.querySelector('ak-stage-identification');
            if (!stage || observedStages.has(stage)) return;
            observedStages.add(stage);
            // A stage can connect before its definition or first Lit render completes.
            void (async () => {
                await customElements.whenDefined(stage.localName);
                await stage.updateComplete;
                observer.observe(stage.shadowRoot, {childList: true, subtree: true});
                sync();
            })().catch(fallback);
        }
        observer.observe(executor.shadowRoot, {childList: true, subtree: true});
        sync();
        observeStage();
    } catch (error) {
        // A presentation failure must leave the working native login available.
        fallback(error);
    }
}
