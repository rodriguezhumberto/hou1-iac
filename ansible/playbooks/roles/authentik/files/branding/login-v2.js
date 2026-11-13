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
        const observedRoots = new WeakSet();
        const pendingElements = new WeakSet();
        const observer = new MutationObserver(() => {
            sync();
            observeFlow(executor.shadowRoot);
        });
        function observeFlow(root) {
            if (!root) return;
            if (!observedRoots.has(root)) {
                observedRoots.add(root);
                observer.observe(root, {childList: true, subtree: true});
            }
            for (const element of root.querySelectorAll('*')) {
                if (!element.localName.includes('-')) continue;
                // Limit shared component overrides to this branded flow, including
                // loading, password and signed-out components in nested shadow roots.
                element.setAttribute('data-pp-flow', '');
                if (element.shadowRoot) observeFlow(element.shadowRoot);
                else if (!pendingElements.has(element)) {
                    pendingElements.add(element);
                    void (async () => {
                        await customElements.whenDefined(element.localName);
                        await element.updateComplete;
                        observeFlow(element.shadowRoot);
                        sync();
                    })().catch(fallback);
                }
            }
        }
        sync();
        observeFlow(executor.shadowRoot);
    } catch (error) {
        // A presentation failure must leave the working native login available.
        fallback(error);
    }
}
