(function () {
    'use strict';
    function create(button, role) {
        var AudioContext = window.AudioContext || window.webkitAudioContext;
        var key = 'marshall-chat-sounds-' + role, enabled = true, context = null;
        var seen = new Map(), failed = false;
        try { enabled = window.localStorage.getItem(key) !== 'off'; }
        catch (error) { console.warn('Chat sound preference could not be read:', error); }
        function render() {
            button.textContent = !AudioContext || failed ? 'Sounds unavailable'
                : !enabled ? 'Sounds off' : context && context.state === 'running' ? 'Sounds on' : 'Enable sounds';
            button.setAttribute('aria-pressed', String(enabled && !!AudioContext && !failed));
            button.title = 'Incoming-message sounds. Browsers require a click or keypress before audio can play.';
            button.disabled = !AudioContext || failed;
        }
        async function unlock() {
            if (!enabled || !AudioContext || failed) return;
            try {
                if (!context) context = new AudioContext();
                if (context.state === 'suspended') await context.resume();
            } catch (error) {
                failed = true;
                console.warn('Chat audio could not be enabled:', error);
            }
            render();
        }
        document.addEventListener('click', unlock);
        document.addEventListener('keydown', unlock);
        button.addEventListener('click', function () {
            if (enabled && (!context || context.state !== 'running')) {
                unlock();
                return;
            }
            enabled = !enabled;
            try { window.localStorage.setItem(key, enabled ? 'on' : 'off'); }
            catch (error) { console.warn('Chat sound preference could not be saved:', error); }
            if (enabled) unlock();
            render();
        });
        window.addEventListener('storage', function (event) {
            if (event.key === key) {
                enabled = event.newValue !== 'off';
                render();
            }
        });
        function play() {
            if (!enabled || !context || context.state !== 'running' || failed) return;
            try {
                var gain = context.createGain(), oscillator = context.createOscillator();
                var now = context.currentTime;
                oscillator.type = 'sine';
                oscillator.frequency.setValueAtTime(660, now);
                oscillator.frequency.setValueAtTime(880, now + 0.1);
                gain.gain.setValueAtTime(0, now);
                gain.gain.linearRampToValueAtTime(0.08, now + 0.015);
                gain.gain.exponentialRampToValueAtTime(0.001, now + 0.28);
                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.onended = function () { oscillator.disconnect(); gain.disconnect(); };
                oscillator.start(now);
                oscillator.stop(now + 0.3);
            } catch (error) {
                failed = true;
                console.warn('Chat notification sound failed:', error);
                render();
            }
        }
        render();
        return {
            observe: function (channel, latest) {
                var previous = seen.get(channel);
                seen.set(channel, Math.max(previous || 0, latest));
                if (previous !== undefined && latest > previous) play();
            },
        };
    }
    window.MarshallChatSounds = { create: create };
})();
