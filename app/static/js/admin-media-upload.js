(function () {
    'use strict';

    const form = document.getElementById('vehicle-form');
    const status = document.getElementById('vehicle-upload-status');
    if (!form || !status) return;
    let uploading = false;

    window.addEventListener('beforeunload', (event) => {
        if (!uploading) return;
        event.preventDefault();
        event.returnValue = '';
    });

    async function send(url, data, headers = {}) {
        const response = await fetch(url, {
            method: 'POST', body: data, credentials: 'same-origin',
            headers: { Accept: 'application/json', ...headers },
        });
        const ray = response.headers.get('cf-ray');
        const rayDetail = ray ? ` Cloudflare Ray: ${ray}.` : '';
        if (!response.headers.get('content-type')?.includes('application/json')) {
            throw new Error(
                `Upload request returned HTTP ${response.status}. Your session may have expired or the server rejected the request.${rayDetail}`
            );
        }
        const result = await response.json();
        if (!response.ok || !result.success) {
            const errors = Object.entries(result.errors || {})
                .map(([name, messages]) => `${name}: ${messages.join(' ')}`).join(' ');
            const detail = typeof result.message === 'string' ? result.message
                : typeof result.error === 'string' ? result.error : 'The server did not confirm the upload.';
            throw new Error(`HTTP ${response.status}: ${detail} ${errors}${rayDetail}`.trim());
        }
        return result;
    }

    form.addEventListener('submit', async (event) => {
        if (event.defaultPrevented) return;
        if (uploading) {
            event.preventDefault();
            return;
        }
        const photos = Array.from(form.elements.images.files || []);
        const removeVideo = form.elements.remove_video?.checked;
        const video = removeVideo ? null : form.elements.video_file.files?.[0];
        if (!photos.length && !video) return;
        event.preventDefault();
        const limit = Number(form.dataset.mediaFileLimit);
        const oversized = [...photos, ...(video ? [video] : [])].find(file => file.size > limit);
        if (oversized) {
            status.className = 'alert alert-danger';
            status.textContent = `${oversized.name} exceeds the ${(limit / 1048576).toFixed(1)} MB per-file limit. `
                + 'Compress that file or use a YouTube/Vimeo URL. Nothing has been saved.';
            return;
        }
        const data = new FormData(form);
        const csrf = data.get('csrf_token');
        const postToFacebook = data.get('post_to_facebook');
        data.delete('images');
        data.delete('video_file');
        const controls = Array.from(form.elements).filter(control => !control.disabled);
        controls.forEach(control => { control.disabled = true; });
        uploading = true;
        let saved = null;
        let completed = 0;
        let currentFile = '';
        status.className = 'alert alert-info';
        status.textContent = 'Saving vehicle details...';
        try {
            saved = await send(form.action, data, { 'X-Vehicle-Media-Upload': 'deferred' });
            for (const [index, photo] of photos.entries()) {
                currentFile = photo.name;
                status.textContent = `Uploading photo ${index + 1} of ${photos.length}: ${photo.name}`;
                const media = new FormData();
                media.set('csrf_token', csrf);
                media.set('images', photo);
                await send(saved.upload_url, media);
                completed++;
            }
            if (video) {
                currentFile = video.name;
                status.textContent = 'Uploading video. Compression will run in the background after upload.';
                const media = new FormData();
                media.set('csrf_token', csrf);
                media.set('video_file', video);
                await send(saved.upload_url, media);
            }
            status.textContent = 'Uploads saved. Finishing vehicle save...';
            currentFile = '';
            const finish = new FormData();
            finish.set('csrf_token', csrf);
            finish.set('is_new', form.dataset.isNew);
            if (postToFacebook) finish.set('post_to_facebook', '1');
            await send(saved.finish_url, finish);
            uploading = false;
            window.location.assign(saved.redirect_url);
        } catch (error) {
            status.className = 'alert alert-danger';
            status.textContent = `${currentFile ? `${currentFile}: ` : ''}${error.message} `;
            if (saved) {
                status.append(`Vehicle details and ${completed} photo(s) were saved. `
                    + 'Check the listing before retrying; the last request may also have reached the server. ');
                const link = document.createElement('a');
                link.href = saved.edit_url;
                link.textContent = 'Open saved vehicle to check media and upload any missing files.';
                status.append(link);
                // Do not replay an ambiguous request or create a second vehicle.
            } else {
                status.append('Check the inventory before submitting again if the server response was interrupted.');
                controls.forEach(control => { control.disabled = false; });
            }
        } finally {
            uploading = false;
        }
    });
})();
