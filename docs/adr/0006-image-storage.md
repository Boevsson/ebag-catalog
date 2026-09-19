# 6. Image storage is a configuration choice

## Context

Product images are uploaded through the API. On one server the local disk is
enough. With several application servers it is not: each server would only see
the files uploaded to it. Production needs shared object storage, usually behind
a CDN, and moving there should not mean changing application code.

## Decision

- The application **never touches the file system itself.** The model's
  `ImageField`, the serializer, the generated URLs and the clean-up of old files
  (`django-cleanup`) all go through Django's storage API.
- The backend is chosen by one environment variable:
  - `MEDIA_STORAGE=local` (default): the local disk, served by Django only in
    development and the demo.
  - `MEDIA_STORAGE=gcs`: a Google Cloud Storage bucket (`GCS_BUCKET_NAME`),
    optionally with a CDN domain in front of it (`GCS_CDN_URL`). Credentials are
    found the standard Google way (`GOOGLE_APPLICATION_CREDENTIALS` or the
    machine's service account), so no secret is in the settings.
- The Google driver (`django-storages[google]`) is a regular dependency, so the
  switch needs no new image build: it really is only configuration.
- In the bucket, URLs are plain (unsigned) and objects are stored with
  `Cache-Control: public, max-age=31536000, immutable`. That is safe because
  file names are random UUIDs and never reused: a new image is a new URL.
- An unknown `MEDIA_STORAGE` value stops the process at start-up instead of
  silently falling back to the disk.

## Consequences

- **Cost:** about twenty extra packages in the image for a driver that is idle
  while `MEDIA_STORAGE=local`. The alternative, an optional dependency, would
  make the switch a rebuild rather than a configuration change.
- Storages are Django's built-in Strategy pattern: one `Storage` interface,
  interchangeable backends, chosen in the settings. The application never asks
  which one it has. `config/settings.py` keeps the available ones as named
  presets (`MEDIA_STORAGES`) and picks one by name, so another provider (S3,
  Azure) is one more preset plus its `django-storages` extra; nothing that
  exists has to be edited.
- What is tested without a Google account: the settings for each environment,
  that the driver accepts exactly those options and builds the expected bucket
  and CDN URLs, and - with an in-memory storage - that upload, replace and delete
  work against a storage that is not the disk. **Not tested:** a real upload to a
  real bucket.
- The bucket must allow public reads (or sit behind the CDN), and the switch does
  not move existing files: copy `media/` into the bucket first.
