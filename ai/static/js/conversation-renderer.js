/**
 * Standardized Conversation Renderer Component
 * 
 * Renders agent conversations consistently across the application
 * Based on the backend.html implementation with enhancements for reusability
 */

class ConversationRenderer {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) {
            throw new Error(`Container with id '${containerId}' not found`);
        }
        
        this.options = {
            showTimestamps: options.showTimestamps || false,
            enableMarkdown: options.enableMarkdown !== false, // default true
            enableToolCallDetails: options.enableToolCallDetails !== false, // default true
            enableChartProcessing: options.enableChartProcessing !== false, // default true
            autoScroll: options.autoScroll !== false, // default true
            className: options.className || 'conversation-renderer',
            ...options
        };
        
        this.toolCallData = new Map();
        this.init();
    }
    
    init() {
        // Add the conversation renderer class
        this.container.classList.add(this.options.className);
        
        // Ensure marked.js is available if markdown is enabled
        if (this.options.enableMarkdown && typeof marked === 'undefined') {
            console.warn('marked.js not available - markdown rendering disabled');
            this.options.enableMarkdown = false;
        }
        
        // Add CSS for map loading spinner if not already added
        if (!document.getElementById('conversation-renderer-styles')) {
            const style = document.createElement('style');
            style.id = 'conversation-renderer-styles';
            style.textContent = `
                @keyframes spin { 
                    0% { transform: rotate(0deg); } 
                    100% { transform: rotate(360deg); } 
                }
                .conversation-renderer .loading-spinner {
                    animation: spin 1s linear infinite;
                }
            `;
            document.head.appendChild(style);
        }
    }
    
    /**
     * Render a complete conversation from session data
     */
    renderConversation(sessionData) {
        this.clear();
        
        if (!sessionData) {
            this.addSystemMessage("No conversation data available");
            return;
        }
        
        // Add session info if available
        if (sessionData.session_id && this.options.showTimestamps) {
            const startTime = sessionData.start_time || sessionData.timestamp;
            const model = sessionData.model || 'Unknown';
            const endTime = sessionData.end_time || '';
            let timeInfo = new Date(startTime).toLocaleString();
            if (endTime) {
                timeInfo += ` - ${new Date(endTime).toLocaleString()}`;
            }
            this.addSystemMessage(`Session: ${sessionData.session_id} | Model: ${model} | ${timeInfo}`);
        }
        
        // Process messages and tool calls in chronological order
        const events = this.buildEventTimeline(sessionData);
        
        // Process events in chronological order
        // console.log('Processing events:', events.length, 'events');
        events.forEach((event, index) => {
            // console.log(`Event ${index}:`, event.type, event);
            switch (event.type) {
                case 'message':
                    this.addMessage(event.content, event.sender, this.options.enableMarkdown, event.timestamp);
                    break;
                case 'tool_call_start':
                    // For session replay, we'll wait for the end event to show the completed state
                    break;
                case 'tool_call_end':
                    // Show completed tool call with details
                    this.addCompletedToolCall(event.tool_name, event.success, event.execution_time_ms, event.response, event.arguments);
                    break;
            }
        });
    }
    
    /**
     * Build a chronological timeline of conversation events
     */
    buildEventTimeline(sessionData) {
        const events = [];
        
        // Choose between conversation array approach or intermediate_responses approach
        // If we have intermediate_responses, use those for a cleaner streaming replay
        // This avoids duplication issues with final_response and conversation array
        const useIntermediateResponses = sessionData.intermediate_responses && 
            Array.isArray(sessionData.intermediate_responses) && 
            sessionData.intermediate_responses.length > 0;
        
        // Debug: Session data analysis (uncomment for debugging)
        // console.log('Session data analysis:', {
        //     hasIntermediateResponses: !!sessionData.intermediate_responses,
        //     intermediateResponsesCount: sessionData.intermediate_responses?.length || 0,
        //     hasConversation: !!sessionData.conversation,
        //     conversationCount: sessionData.conversation?.length || 0,
        //     hasToolCalls: !!sessionData.tool_calls,
        //     toolCallsCount: sessionData.tool_calls?.length || 0,
        //     useIntermediateResponses: useIntermediateResponses
        // });
        
        if (useIntermediateResponses) {
            // Use intermediate responses approach - properly interleaved with tool calls
            
            // First add user message if present
            if (sessionData.user_input) {
                events.push({
                    type: 'message',
                    content: sessionData.user_input,
                    sender: 'user',
                    timestamp: sessionData.start_time || sessionData.timestamp
                });
            }
            
            // Collect all intermediate responses as events
            const responseEvents = [];
            sessionData.intermediate_responses.forEach((response, index) => {
                responseEvents.push({
                    type: 'message',
                    content: response.content,
                    sender: 'assistant',
                    timestamp: response.timestamp
                });
            });
            
            // Collect all tool calls as events
            const toolCallEvents = [];
            if (sessionData.tool_calls && Array.isArray(sessionData.tool_calls)) {
                sessionData.tool_calls.forEach((toolCall, index) => {
                    // Keep timestamp in original format for proper sorting
                    // Convert to ISO only when needed for display
                    let timestamp = toolCall.start_time;
                    if (!timestamp) {
                        timestamp = sessionData.start_time || new Date().toISOString();
                    }
                    
                    console.log(`Tool call ${index} (${toolCall.tool_name}): raw=${toolCall.start_time}, type=${typeof toolCall.start_time}`);
                    
                    toolCallEvents.push({
                        type: 'tool_call_end',  // Show completed tool calls
                        tool_name: toolCall.tool_name,
                        tool_id: `tool_${toolCall.tool_name}_${index}`,
                        success: toolCall.success,
                        response: toolCall.result,
                        arguments: toolCall.arguments,
                        execution_time_ms: toolCall.execution_time_ms,
                        timestamp: timestamp
                    });
                });
            }
            
            // Debug: Log intermediate response timestamps
            console.log('Intermediate responses:', responseEvents.map((e, i) => ({
                index: i,
                timestamp: e.timestamp,
                content_preview: e.content.substring(0, 50)
            })));
            
            // Merge tool calls and responses into events array with indices for stable sorting
            responseEvents.forEach((e, i) => { e._originalIndex = i; events.push(e); });
            toolCallEvents.forEach((e, i) => { e._originalIndex = i + responseEvents.length; events.push(e); });
            
            // Debug: Log before sorting
            console.log('Before sorting:', events.map(e => ({
                type: e.type,
                timestamp: e.timestamp,
                tool_name: e.tool_name,
                originalIndex: e._originalIndex
            })));
        } else {
            // Fallback to conversation array approach (for sessions without intermediate_responses)
            if (sessionData.conversation && Array.isArray(sessionData.conversation)) {
                let toolCallIndex = 0;
                
                sessionData.conversation.forEach((msg, index) => {
                    if (msg.role === 'tool_call') {
                        // Handle tool call messages
                        const toolName = msg.content.replace('Tool: ', '');
                        let toolCallDetails = sessionData.tool_calls && toolCallIndex < sessionData.tool_calls.length ? 
                            sessionData.tool_calls[toolCallIndex] : null;
                        
                        if (toolCallDetails && toolCallDetails.tool_name === toolName) {
                            toolCallIndex++;
                        } else {
                            toolCallDetails = sessionData.tool_calls ? 
                                sessionData.tool_calls.find(tc => tc.tool_name === toolName) : null;
                        }
                        
                        if (toolCallDetails) {
                            events.push({
                                type: 'tool_call_end',
                                tool_name: toolCallDetails.tool_name,
                                tool_id: `tool_${toolCallDetails.tool_name}_${index}`,
                                success: toolCallDetails.success,
                                response: toolCallDetails.result,
                                arguments: toolCallDetails.arguments,
                                execution_time_ms: toolCallDetails.execution_time_ms,
                                timestamp: msg.timestamp
                            });
                        }
                    } else {
                        // Handle regular messages (user, assistant)
                        events.push({
                            type: 'message',
                            content: msg.content,
                            sender: msg.role,
                            timestamp: msg.timestamp
                        });
                    }
                });
            }
            
            // If no conversation array either, try final_response as last resort
            if ((!sessionData.conversation || sessionData.conversation.length === 0) && 
                sessionData.final_response && typeof sessionData.final_response === 'string' && 
                sessionData.final_response.trim()) {
                events.push({
                    type: 'message',
                    content: sessionData.final_response.trim(),
                    sender: 'assistant',
                    timestamp: sessionData.end_time || sessionData.timestamp || new Date().toISOString()
                });
            }
        }
        
        // Sort events by timestamp if available
        events.sort((a, b) => {
            if (a.timestamp && b.timestamp) {
                // Convert all timestamps to milliseconds for comparison
                let timeA, timeB;
                
                // Handle timestamp A
                if (typeof a.timestamp === 'number') {
                    // Assume Unix timestamp in seconds if < large number, otherwise milliseconds
                    timeA = a.timestamp < 1e10 ? a.timestamp * 1000 : a.timestamp;
                } else if (typeof a.timestamp === 'string') {
                    // ISO string
                    timeA = new Date(a.timestamp).getTime();
                } else {
                    timeA = 0;
                }
                
                // Handle timestamp B
                if (typeof b.timestamp === 'number') {
                    // Assume Unix timestamp in seconds if < large number, otherwise milliseconds
                    timeB = b.timestamp < 1e10 ? b.timestamp * 1000 : b.timestamp;
                } else if (typeof b.timestamp === 'string') {
                    // ISO string
                    timeB = new Date(b.timestamp).getTime();
                } else {
                    timeB = 0;
                }
                
                // If dates are invalid (NaN), put them at the end
                if (isNaN(timeA) && isNaN(timeB)) {
                    // Use original index as tiebreaker on reload
                    return (a._originalIndex || 0) - (b._originalIndex || 0);
                }
                if (isNaN(timeA)) return 1;
                if (isNaN(timeB)) return -1;
                
                // If timestamps are equal (within 1 second), preserve original order
                const diff = timeA - timeB;
                if (Math.abs(diff) < 1000) {
                    return (a._originalIndex || 0) - (b._originalIndex || 0);
                }
                
                return diff;
            }
            // If one has timestamp and other doesn't, timestamp comes first
            if (a.timestamp && !b.timestamp) return -1;
            if (!a.timestamp && b.timestamp) return 1;
            return 0;
        });
        
        // Debug: Log event order (uncomment to debug)
        console.log('Sorted events:', events.map(e => ({
            type: e.type,
            timestamp: e.timestamp,
            tool_name: e.tool_name,
            content_preview: e.content ? e.content.substring(0, 50) : null
        })));
        
        return events;
    }
    
    /**
     * Add a message to the conversation
     */
    addMessage(content, sender = 'assistant', isMarkdown = true, timestamp = null) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `conversation-message ${sender}`;
        
        if (this.options.showTimestamps) {
            const timestampDiv = document.createElement('div');
            timestampDiv.className = 'conversation-timestamp';
            if (timestamp) {
                // Handle Unix timestamp (seconds) vs ISO string
                let date;
                if (typeof timestamp === 'number') {
                    // Assume Unix timestamp in seconds if < large number
                    date = new Date(timestamp < 1e10 ? timestamp * 1000 : timestamp);
                } else {
                    date = new Date(timestamp);
                }
                timestampDiv.textContent = date.toLocaleTimeString();
            } else {
                timestampDiv.textContent = new Date().toLocaleTimeString();
            }
            messageDiv.appendChild(timestampDiv);
        }
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'conversation-content';
        
        // Store the raw content for streaming updates
        messageDiv._rawContent = content;
        
        // Initialize chart tracking for assistant messages
        if (sender === 'assistant') {
            messageDiv._renderedCharts = new Set();
        }
        
        // Process content
        let processedContent = content;
        if (sender === 'assistant' && this.options.enableChartProcessing) {
            // For initial message creation, render charts immediately and mark them as rendered
            processedContent = this.processChartPlaceholders(content);
            
            // Track rendered charts
            if (messageDiv._renderedCharts) {
                const chartRegex = /\[CHART:(\w+):([a-zA-Z0-9\-:]+)\]/g;
                let match;
                while ((match = chartRegex.exec(content)) !== null) {
                    const [fullMatch, chartType, params] = match;
                    const chartId = `${chartType}:${params}`;
                    messageDiv._renderedCharts.add(chartId);
                }
                
                // Also track dual map charts
                const dualMapRegex = /\[CHART:dualmap:([a-zA-Z0-9\-]+):([a-zA-Z0-9\-]+)\]/g;
                while ((match = dualMapRegex.exec(content)) !== null) {
                    const [fullMatch, map1Id, map2Id] = match;
                    const chartId = `dualmap:${map1Id}:${map2Id}`;
                    messageDiv._renderedCharts.add(chartId);
                }
            }
        }
        
        if (isMarkdown && this.options.enableMarkdown) {
            contentDiv.innerHTML = marked.parse(processedContent);
        } else {
            // Convert newlines to <br> tags for non-markdown content
            processedContent = processedContent.replace(/\n/g, '<br>');
            contentDiv.innerHTML = processedContent;
        }
        
        messageDiv.appendChild(contentDiv);
        this.container.appendChild(messageDiv);
        
        if (this.options.autoScroll) {
            this.scrollToBottom();
        }
        
        return messageDiv;
    }
    
    /**
     * Append content to an existing message (for streaming)
     */
    appendToMessage(messageDiv, additionalContent) {
        if (!messageDiv || messageDiv._rawContent === undefined) {
            console.warn('Cannot append to message: invalid message element');
            return;
        }
        
        // Accumulate the raw content
        messageDiv._rawContent += additionalContent;
        
        // Use smart chart rendering to preserve existing charts
        this.updateMessageContentWithChartPreservation(messageDiv, messageDiv._rawContent);
        
        if (this.options.autoScroll) {
            this.scrollToBottom();
        }
    }
    
    /**
     * Smart chart rendering function that preserves existing charts during streaming
     */
    updateMessageContentWithChartPreservation(messageDiv, content) {
        const contentDiv = messageDiv.querySelector('.conversation-content');
        if (!contentDiv) return;
        
        // Initialize chart tracking if not exists
        if (!messageDiv._renderedCharts) {
            messageDiv._renderedCharts = new Set();
        }
        
        // Process content to find chart placeholders
        const chartPlaceholders = [];
        const chartRegex = /\[CHART:(\w+):([a-zA-Z0-9\-:]+)\]/g;
        let match;
        while ((match = chartRegex.exec(content)) !== null) {
            const [fullMatch, chartType, params] = match;
            const chartId = `${chartType}:${params}`;
            chartPlaceholders.push({
                fullMatch,
                chartType,
                params,
                chartId,
                startIndex: match.index,
                endIndex: match.index + fullMatch.length
            });
        }
        
        // Handle dual map placeholders separately
        const dualMapRegex = /\[CHART:dualmap:([a-zA-Z0-9\-]+):([a-zA-Z0-9\-]+)\]/g;
        while ((match = dualMapRegex.exec(content)) !== null) {
            const [fullMatch, map1Id, map2Id] = match;
            const chartId = `dualmap:${map1Id}:${map2Id}`;
            chartPlaceholders.push({
                fullMatch,
                chartType: 'dualmap',
                params: `${map1Id}:${map2Id}`,
                chartId,
                startIndex: match.index,
                endIndex: match.index + fullMatch.length
            });
        }
        
        // Preserve existing chart elements
        const existingCharts = new Map();
        chartPlaceholders.forEach(placeholder => {
            if (messageDiv._renderedCharts.has(placeholder.chartId)) {
                // Find existing chart element
                const existingChart = contentDiv.querySelector(`[data-chart-id="${placeholder.chartId}"]`);
                if (existingChart) {
                    existingCharts.set(placeholder.chartId, existingChart.cloneNode(true));
                }
            }
        });
        
        // Process chart placeholders - render new ones immediately, preserve existing ones
        let processedContent = content;
        chartPlaceholders.forEach(placeholder => {
            if (!messageDiv._renderedCharts.has(placeholder.chartId)) {
                // New chart - render it immediately during streaming
                const chartHtml = this.generateChartHtml(placeholder.chartType, placeholder.params, placeholder.chartId);
                if (chartHtml) {
                    processedContent = processedContent.replace(placeholder.fullMatch, chartHtml);
                    // Mark this chart as rendered
                    messageDiv._renderedCharts.add(placeholder.chartId);
                }
            } else {
                // Existing chart - use placeholder that will be replaced with preserved element
                const placeholderHtml = `<div class="chart-placeholder" data-chart-id="${placeholder.chartId}"></div>`;
                processedContent = processedContent.replace(placeholder.fullMatch, placeholderHtml);
            }
        });
        
        // Update content with markdown parsing
        if (this.options.enableMarkdown && typeof marked !== 'undefined') {
            contentDiv.innerHTML = marked.parse(processedContent);
        } else {
            contentDiv.innerHTML = processedContent.replace(/\n/g, '<br>');
        }
        
        // Restore preserved chart elements
        existingCharts.forEach((chartElement, chartId) => {
            const placeholder = contentDiv.querySelector(`[data-chart-id="${chartId}"].chart-placeholder`);
            if (placeholder) {
                placeholder.replaceWith(chartElement);
            }
        });
    }
    
    
    /**
     * Generate chart HTML for a specific chart type
     */
    generateChartHtml(chartType, params, chartId) {
        if (chartType === 'dualmap') {
            const [map1Id, map2Id] = params.split(':');
            return `<div class="chart-container" data-chart-id="${chartId}">
                <iframe src="/dual-map?map1_id=${map1Id}&map2_id=${map2Id}" width="100%" height="400" frameborder="0"></iframe>
                <div class="chart-caption">Dual Map Comparison: ${map1Id} vs ${map2Id}</div>
            </div>`;
        } else if (chartType === 'time_series_id') {
            return `<div class="chart-container" data-chart-id="${chartId}">
                <iframe src="/backend/time-series-chart?chart_id=${params}" width="100%" height="400" frameborder="0"></iframe>
                <div class="chart-caption">Chart ${params} (${chartType})</div>
            </div>`;
        } else if (chartType === 'time_series') {
            const paramParts = params.split(':');
            if (paramParts.length === 3) {
                const [metric_id, district_id, period_type] = paramParts;
                return `<div class="chart-container" data-chart-id="${chartId}">
                    <iframe src="/backend/time-series-chart?metric_id=${metric_id}&district_id=${district_id}&period_type=${period_type}" width="100%" height="400" frameborder="0"></iframe>
                    <div class="chart-caption">Chart ${params} (${chartType})</div>
                </div>`;
            } else {
                return `<div class="chart-container" data-chart-id="${chartId}">
                    <iframe src="/backend/time-series-chart?chart_id=${params}" width="100%" height="400" frameborder="0"></iframe>
                    <div class="chart-caption">Chart ${params} (${chartType})</div>
                </div>`;
            }
        } else if (chartType === 'map') {
            // For maps, show a fast-loading preview first, then load full map
            const mapHtml = `<div class="chart-container map-container" data-chart-id="${chartId}" data-map-id="${params}">
                <div class="map-preview" style="width: 100%; height: 400px; background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; position: relative; border: 1px solid #ddd;">
                    <div style="text-align: center; color: #1976d2;">
                        <div style="font-size: 24px; margin-bottom: 8px;">🗺️</div>
                        <div style="font-weight: 600; margin-bottom: 4px;">Loading Interactive Map</div>
                        <div style="font-size: 12px; opacity: 0.8;">Map ID: ${params}</div>
                        <div class="loading-spinner" style="margin-top: 12px; width: 20px; height: 20px; border: 2px solid #1976d2; border-top: 2px solid transparent; border-radius: 50%; animation: spin 1s linear infinite; margin: 12px auto 0;"></div>
                    </div>
                </div>
                <div class="chart-caption">Chart ${params} (${chartType})</div>
            </div>`;
            
            // Load the actual map after a brief delay to avoid blocking the stream
            setTimeout(() => {
                const mapContainer = document.querySelector(`[data-chart-id="${chartId}"]`);
                if (mapContainer) {
                    const preview = mapContainer.querySelector('.map-preview');
                    if (preview) {
                        preview.innerHTML = `<iframe src="/backend/map-chart?id=${params}" 
                                                    style="width: 100%; height: 100%; border: none; border-radius: 8px;" 
                                                    frameborder="0" scrolling="no">
                                            </iframe>`;
                    }
                }
            }, 500); // 500ms delay to let streaming continue smoothly
            
            return mapHtml;
        } else if (chartType === 'anomaly') {
            return `<div class="chart-container" data-chart-id="${chartId}">
                <iframe src="/anomaly-analyzer/anomaly-chart?id=${params}" width="100%" height="400" frameborder="0"></iframe>
                <div class="chart-caption">Chart ${params} (${chartType})</div>
            </div>`;
        } else {
            // Fallback to generic chart endpoint
            return `<div class="chart-container" data-chart-id="${chartId}">
                <iframe src="/backend/charts/${chartType}/${params}" width="100%" height="400" frameborder="0"></iframe>
                <div class="chart-caption">Chart ${params} (${chartType})</div>
            </div>`;
        }
    }
    
    /**
     * Add a completed tool call with details (for session replay)
     */
    addCompletedToolCall(toolName, success = true, executionTimeMs = null, response = null, args = null) {
        const toolId = `tool-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const toolCallDiv = document.createElement('div');
        toolCallDiv.id = toolId;
        toolCallDiv.className = `conversation-tool-call ${success ? 'completed' : 'error'}`;
        
        // Create content container
        const contentDiv = document.createElement('div');
        contentDiv.className = 'conversation-tool-call-content';
        
        const toolNameDiv = document.createElement('div');
        toolNameDiv.className = 'tool-name';
        toolNameDiv.textContent = `🔧 ${toolName}`;
        
        const toolStatusDiv = document.createElement('div');
        toolStatusDiv.className = 'tool-status';
        let statusText = success ? 'Success' : 'Failed';
        if (executionTimeMs) {
            statusText += ` (${executionTimeMs}ms)`;
        }
        toolStatusDiv.textContent = statusText;
        
        // Add queryURL link if available
        if (response && response.queryURL) {
            const queryUrlDiv = document.createElement('div');
            queryUrlDiv.className = 'tool-query-url';
            const link = document.createElement('a');
            link.href = response.queryURL;
            link.target = '_blank';
            link.textContent = '🔗 View Query';
            link.title = 'Click to view the actual API query in a new tab';
            queryUrlDiv.appendChild(link);
            contentDiv.appendChild(queryUrlDiv);
        }
        
        contentDiv.appendChild(toolNameDiv);
        contentDiv.appendChild(toolStatusDiv);
        toolCallDiv.appendChild(contentDiv);
        
        // Create details container if enabled
        if (this.options.enableToolCallDetails) {
            const detailsDiv = document.createElement('div');
            detailsDiv.className = 'conversation-tool-call-details';
            detailsDiv.innerHTML = `
                <h4>Tool Call Details</h4>
                <div><strong>Function:</strong> ${toolName}</div>
                <div><strong>Status:</strong> ${statusText}</div>
                <div><strong>Arguments:</strong> <pre>${args ? JSON.stringify(args, null, 2) : 'N/A'}</pre></div>
                <div><strong>Response:</strong> <pre>${response ? JSON.stringify(response, null, 2) : (success ? 'Success' : 'Failed')}</pre></div>
            `;
            toolCallDiv.appendChild(detailsDiv);
            
            // Add click handler to toggle details
            contentDiv.addEventListener('click', function() {
                detailsDiv.classList.toggle('show');
            });
        }
        
        this.container.appendChild(toolCallDiv);
        
        if (this.options.autoScroll) {
            this.scrollToBottom();
        }
        
        return toolCallDiv;
    }

    /**
     * Add a tool call to the conversation
     */
    addToolCall(toolName, toolId = null, response = null) {
        const id = toolId || `tool-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        
        const toolCallDiv = document.createElement('div');
        toolCallDiv.id = id;
        toolCallDiv.className = 'conversation-tool-call running';
        
        // Create content container
        const contentDiv = document.createElement('div');
        contentDiv.className = 'conversation-tool-call-content';
        
        const toolNameDiv = document.createElement('div');
        toolNameDiv.className = 'tool-name';
        toolNameDiv.textContent = `🔧 ${toolName}`;
        
        const toolStatusDiv = document.createElement('div');
        toolStatusDiv.className = 'tool-status';
        toolStatusDiv.textContent = 'Running';
        
        // Add queryURL link if available
        if (response && response.queryURL) {
            const queryUrlDiv = document.createElement('div');
            queryUrlDiv.className = 'tool-query-url';
            const link = document.createElement('a');
            link.href = response.queryURL;
            link.target = '_blank';
            link.textContent = '🔗 View Query';
            link.title = 'Click to view the actual API query in a new tab';
            queryUrlDiv.appendChild(link);
            contentDiv.appendChild(queryUrlDiv);
        }
        
        contentDiv.appendChild(toolNameDiv);
        contentDiv.appendChild(toolStatusDiv);
        toolCallDiv.appendChild(contentDiv);
        
        // Create details container if enabled
        if (this.options.enableToolCallDetails) {
            const detailsDiv = document.createElement('div');
            detailsDiv.className = 'conversation-tool-call-details';
            detailsDiv.innerHTML = `
                <h4>Tool Call Details</h4>
                <div><strong>Function:</strong> ${toolName}</div>
                <div><strong>Status:</strong> Running...</div>
                <div><strong>Arguments:</strong> <pre id="${id}-args">Loading...</pre></div>
                <div><strong>Response:</strong> <pre id="${id}-response">Waiting for completion...</pre></div>
            `;
            toolCallDiv.appendChild(detailsDiv);
            
            // Add click handler to toggle details
            contentDiv.addEventListener('click', function() {
                detailsDiv.classList.toggle('show');
            });
        }
        
        this.container.appendChild(toolCallDiv);
        
        // Store tool call data
        this.toolCallData.set(id, {
            name: toolName,
            status: 'running',
            arguments: null,
            response: null,
            startTime: Date.now()
        });
        
        if (this.options.autoScroll) {
            this.scrollToBottom();
        }
        
        return id;
    }
    
    /**
     * Complete a tool call
     */
    completeToolCall(toolId, success = true, response = null, executionTimeMs = null) {
        const toolCallDiv = document.getElementById(toolId);
        if (!toolCallDiv) return;
        
        // Update visual state
        toolCallDiv.className = `conversation-tool-call ${success ? 'completed' : 'error'}`;
        
        const statusDiv = toolCallDiv.querySelector('.tool-status');
        if (statusDiv) {
            let statusText = success ? 'Success' : 'Failure';
            if (executionTimeMs) {
                statusText += ` (${executionTimeMs}ms)`;
            }
            statusDiv.textContent = statusText;
        }
        
        // Add queryURL link if available and not already added
        if (response && response.queryURL && !toolCallDiv.querySelector('.tool-query-url')) {
            const contentDiv = toolCallDiv.querySelector('.conversation-tool-call-content');
            if (contentDiv) {
                const queryUrlDiv = document.createElement('div');
                queryUrlDiv.className = 'tool-query-url';
                const link = document.createElement('a');
                link.href = response.queryURL;
                link.target = '_blank';
                link.textContent = '🔗 View Query';
                link.title = 'Click to view the actual API query in a new tab';
                queryUrlDiv.appendChild(link);
                contentDiv.appendChild(queryUrlDiv);
            }
        }
        
        // Update details if enabled
        if (this.options.enableToolCallDetails) {
            const detailsDiv = toolCallDiv.querySelector('.conversation-tool-call-details');
            if (detailsDiv) {
                const statusText = detailsDiv.querySelector('div:nth-child(2)');
                if (statusText) {
                    let status = success ? 'Success' : 'Failure';
                    if (executionTimeMs) {
                        status += ` (${executionTimeMs}ms)`;
                    }
                    statusText.innerHTML = `<strong>Status:</strong> ${status}`;
                }
                
                const responseElement = detailsDiv.querySelector(`#${toolId}-response`);
                if (responseElement) {
                    responseElement.textContent = response ? JSON.stringify(response, null, 2) : (success ? 'Success' : 'Failure');
                }
            }
        }
        
        // Update stored data
        if (this.toolCallData.has(toolId)) {
            const data = this.toolCallData.get(toolId);
            data.status = success ? 'success' : 'failure';
            data.response = response;
            data.endTime = Date.now();
            data.duration = data.endTime - data.startTime;
        }
        
        // Update context window status after tool call completion if function is available
        if (typeof window.fetchContextWindowStatus === 'function') {
            console.log('Tool call completed in conversation renderer, updating context window status');
            window.fetchContextWindowStatus();
        }
    }
    
    /**
     * Update tool call arguments
     */
    updateToolCallArguments(toolId, args) {
        if (!this.options.enableToolCallDetails) return;
        
        const argsElement = document.getElementById(`${toolId}-args`);
        if (argsElement) {
            argsElement.textContent = JSON.stringify(args, null, 2);
        }
        
        // Update stored data
        if (this.toolCallData.has(toolId)) {
            this.toolCallData.get(toolId).arguments = args;
        }
    }
    
    /**
     * Add a system message
     */
    addSystemMessage(content) {
        return this.addMessage(content, 'system', false);
    }
    
    /**
     * Clear the conversation
     */
    clear() {
        this.container.innerHTML = '';
        this.toolCallData.clear();
    }
    
    /**
     * Scroll to bottom of conversation
     */
    scrollToBottom() {
        this.container.scrollTop = this.container.scrollHeight;
    }
    
    /**
     * Process chart placeholders (from backend.html)
     */
    processChartPlaceholders(content) {
        if (!this.options.enableChartProcessing) return content;
        
        // First handle dual map placeholders with their specific pattern
        content = content.replace(/\[CHART:dualmap:([a-zA-Z0-9\-]+):([a-zA-Z0-9\-]+)\]/g, (match, map1Id, map2Id) => {
            const chartUrl = `/dual-map?map1_id=${map1Id}&map2_id=${map2Id}`;
            const chartId = `dualmap:${map1Id}:${map2Id}`;
            return `<div class="chart-container" data-chart-id="${chartId}">
                <iframe src="${chartUrl}" width="100%" height="400" frameborder="0"></iframe>
                <div class="chart-caption">Dual Map Comparison: ${map1Id} vs ${map2Id}</div>
            </div>`;
        });
        
        // Then handle regular chart placeholders
        return content.replace(/\[CHART:(\w+):([a-zA-Z0-9\-:]+)\]/g, (match, type, params) => {
            // Generate correct URLs based on chart type
            let chartUrl;
            const chartId = `${type}:${params}`;
            if (type === 'time_series_id') {
                chartUrl = `/backend/time-series-chart?chart_id=${params}`;
            } else if (type === 'map') {
                chartUrl = `/backend/map-chart?id=${params}`;
            } else if (type === 'anomaly') {
                chartUrl = `/anomaly-analyzer/anomaly-chart?id=${params}`;
            } else if (type === 'time_series') {
                // Handle time_series with parameters (metric_id:district_id:period_type)
                const paramParts = params.split(':');
                if (paramParts.length === 3) {
                    const [metric_id, district_id, period_type] = paramParts;
                    chartUrl = `/backend/time-series-chart?metric_id=${metric_id}&district_id=${district_id}&period_type=${period_type}`;
                } else {
                    // Fallback for malformed time_series parameters
                    chartUrl = `/backend/time-series-chart?chart_id=${params}`;
                }
            } else {
                // Fallback to generic chart endpoint
                chartUrl = `/backend/charts/${type}/${params}`;
            }
            
            return `<div class="chart-container" data-chart-id="${chartId}">
                <iframe src="${chartUrl}" width="100%" height="400" frameborder="0"></iframe>
                <div class="chart-caption">Chart ${params} (${type})</div>
            </div>`;
        });
    }
    
    /**
     * Export conversation data
     */
    exportData() {
        return {
            toolCalls: Object.fromEntries(this.toolCallData),
            html: this.container.innerHTML,
            timestamp: new Date().toISOString()
        };
    }
    
    /**
     * Get conversation summary
     */
    getSummary() {
        const messages = this.container.querySelectorAll('.conversation-message');
        const toolCalls = this.container.querySelectorAll('.conversation-tool-call');
        
        return {
            messageCount: messages.length,
            toolCallCount: toolCalls.length,
            successfulToolCalls: this.container.querySelectorAll('.conversation-tool-call.completed').length,
            failedToolCalls: this.container.querySelectorAll('.conversation-tool-call.error').length
        };
    }
}

// Global utility functions for backwards compatibility
window.ConversationRenderer = ConversationRenderer;

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ConversationRenderer;
}
