/**
 * Debug logging utility
 * Only logs when APP_DEBUG is enabled (via meta tag or localStorage)
 */

const Debug = {
    /**
     * Check if debug mode is enabled
     * Checks:
     * 1. localStorage 'herald_debug' flag
     * 2. Meta tag 'herald-debug' content
     * 3. URL parameter ?debug=true
     */
    isEnabled() {
        // Check localStorage
        if (localStorage.getItem('herald_debug') === 'true') {
            return true;
        }
        
        // Check meta tag
        const metaTag = document.querySelector('meta[name="herald-debug"]');
        if (metaTag && metaTag.content === 'true') {
            return true;
        }
        
        // Check URL parameter (for one-time debug session)
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('debug') === 'true') {
            return true;
        }
        
        return false;
    },
    
    /**
     * Conditional console.log
     */
    log(...args) {
        if (this.isEnabled()) {
            console.log('[Herald Debug]', ...args);
        }
    },
    
    /**
     * Conditional console.warn
     */
    warn(...args) {
        if (this.isEnabled()) {
            console.warn('[Herald Debug]', ...args);
        }
    },
    
    /**
     * Conditional console.error (always logs errors)
     */
    error(...args) {
        // Errors are always logged, but with debug prefix if enabled
        if (this.isEnabled()) {
            console.error('[Herald Debug]', ...args);
        } else {
            console.error('[Herald]', ...args);
        }
    },
    
    /**
     * Conditional console.debug
     */
    debug(...args) {
        if (this.isEnabled()) {
            console.debug('[Herald Debug]', ...args);
        }
    },
    
    /**
     * Enable debug mode programmatically
     */
    enable() {
        localStorage.setItem('herald_debug', 'true');
    },
    
    /**
     * Disable debug mode programmatically
     */
    disable() {
        localStorage.removeItem('herald_debug');
    }
};
