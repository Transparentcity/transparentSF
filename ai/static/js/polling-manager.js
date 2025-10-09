/**
 * Centralized Polling Manager for TransparentSF
 * 
 * Prevents memory leaks by tracking and cleaning up all intervals
 * Provides utilities for safe polling with exponential backoff
 */

class PollingManager {
    constructor() {
        this.activeIntervals = new Set();
        this.activeTimeouts = new Set();
        this.pollingStates = new Map(); // Track polling state per key
        
        // Cleanup on page unload
        window.addEventListener('beforeunload', () => {
            this.cleanup();
        });
        
        // Also cleanup on page hide (mobile/tab switching)
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.pauseAllPolling();
            } else {
                this.resumeAllPolling();
            }
        });
    }
    
    /**
     * Create a tracked interval that will be cleaned up automatically
     */
    createInterval(callback, delay, key = null) {
        const id = setInterval(callback, delay);
        this.activeIntervals.add(id);
        
        if (key) {
            this.pollingStates.set(key, {
                id,
                callback,
                delay,
                paused: false
            });
        }
        
        return id;
    }
    
    /**
     * Create a tracked timeout that will be cleaned up automatically
     */
    createTimeout(callback, delay, key = null) {
        const id = setTimeout(callback, delay);
        this.activeTimeouts.add(id);
        
        if (key) {
            this.pollingStates.set(key, {
                id,
                callback,
                delay,
                paused: false
            });
        }
        
        return id;
    }
    
    /**
     * Clear a specific interval by ID
     */
    clearInterval(id) {
        if (this.activeIntervals.has(id)) {
            clearInterval(id);
            this.activeIntervals.delete(id);
        }
    }
    
    /**
     * Clear a specific timeout by ID
     */
    clearTimeout(id) {
        if (this.activeTimeouts.has(id)) {
            clearTimeout(id);
            this.activeTimeouts.delete(id);
        }
    }
    
    /**
     * Clear polling by key
     */
    clearPolling(key) {
        const state = this.pollingStates.get(key);
        if (state) {
            this.clearInterval(state.id);
            this.pollingStates.delete(key);
        }
    }
    
    /**
     * Pause all polling (useful when page is hidden)
     */
    pauseAllPolling() {
        this.pollingStates.forEach((state, key) => {
            if (!state.paused) {
                this.clearInterval(state.id);
                state.paused = true;
            }
        });
    }
    
    /**
     * Resume all polling (useful when page becomes visible)
     */
    resumeAllPolling() {
        this.pollingStates.forEach((state, key) => {
            if (state.paused) {
                const id = this.createInterval(state.callback, state.delay, key);
                state.id = id;
                state.paused = false;
            }
        });
    }
    
    /**
     * Cleanup all intervals and timeouts
     */
    cleanup() {
        this.activeIntervals.forEach(id => clearInterval(id));
        this.activeTimeouts.forEach(id => clearTimeout(id));
        this.activeIntervals.clear();
        this.activeTimeouts.clear();
        this.pollingStates.clear();
    }
    
    /**
     * Create a polling function with exponential backoff
     */
    createPollingWithBackoff(key, callback, options = {}) {
        const {
            initialDelay = 2000,
            maxDelay = 30000,
            backoffMultiplier = 1.5,
            maxAttempts = 10
        } = options;
        
        let attempt = 0;
        let currentDelay = initialDelay;
        
        const poll = async () => {
            try {
                const result = await callback();
                
                // Reset backoff on success
                attempt = 0;
                currentDelay = initialDelay;
                
                return result;
            } catch (error) {
                attempt++;
                
                if (attempt >= maxAttempts) {
                    console.error(`Polling failed after ${maxAttempts} attempts:`, error);
                    this.clearPolling(key);
                    return null;
                }
                
                // Exponential backoff
                currentDelay = Math.min(currentDelay * backoffMultiplier, maxDelay);
                console.warn(`Polling attempt ${attempt} failed, retrying in ${currentDelay}ms:`, error);
                
                // Schedule next attempt
                this.createTimeout(poll, currentDelay, `${key}_retry_${attempt}`);
            }
        };
        
        // Start polling
        this.createInterval(poll, initialDelay, key);
    }
    
    /**
     * Get count of active intervals (for debugging)
     */
    getActiveCount() {
        return {
            intervals: this.activeIntervals.size,
            timeouts: this.activeTimeouts.size,
            pollingStates: this.pollingStates.size
        };
    }
}

// Create global instance
window.pollingManager = new PollingManager();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PollingManager;
}

