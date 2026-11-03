/**
 * Locate the local scanner agent, including its fallback ports.
 */
(function () {
    'use strict';

    const ports = Array.from({ length: 11 }, (_, index) => 8765 + index);
    let connection;

    async function connect() {
        if (connection) return connection;
        connection = (async () => {
            for (const port of ports) {
                const baseUrl = `http://127.0.0.1:${port}`;
                let response;
                try {
                    response = await fetch(`${baseUrl}/printers`, { cache: 'no-store' });
                } catch (fetchError) {
                    continue;
                }
                let payload;
                try {
                    payload = await response.json();
                } catch (parseError) {
                    continue;
                }
                if (payload && Array.isArray(payload.printers)) {
                    if (!response.ok) throw new Error(payload.error || 'Could not list scanners.');
                    return { baseUrl, printers: payload.printers };
                }
                if (response.status === 503 && payload && payload.error) throw new Error(payload.error);
            }
            throw new Error('Cannot connect to the local scanner service. Start it on this computer with “python3 scripts/local_printer_agent.py”, then refresh this page. The agent will use the next available port if 8765 is already in use.');
        })();
        try {
            return await connection;
        } catch (error) {
            connection = null;
            throw error;
        }
    }

    window.LocalScannerAgent = {
        async listPrinters() {
            const agent = await connect();
            return agent.printers;
        },
        async scan(printerId, purpose) {
            const agent = await connect();
            return fetch(`${agent.baseUrl}/scan`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ printer_id: printerId, purpose }),
            });
        },
    };
})();
