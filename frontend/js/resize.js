// Downsizes a receipt photo before upload (decision 0027). No network calls here.
//
// A photo over 2 MB, or with a long side over 2,000 px, becomes a JPEG with the long side
// at most 2,000 px (quality 0.85), with its EXIF orientation applied. It is never upscaled.
// Anything that fails (no createImageBitmap, a decode error, a null blob) gives back the
// original file unchanged, so the upload still happens.

/** The API's `MAX_UPLOAD_MB` default (10); the contract doesn't expose the configured value. */
export const MAX_UPLOAD_MB = 10;
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;

const RESIZE_ABOVE_BYTES = 2 * 1024 * 1024;
const MAX_LONG_SIDE = 2000;
const JPEG_QUALITY = 0.85;
/** The types the API accepts as they are (contracts: jpeg, png, webp). */
const ACCEPTED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

/** The file's name with a `.jpg` extension instead of its own. */
function jpegName(name) {
  const base = (name || "").replace(/\.[^./\\]*$/, "");
  return `${base || "receipt"}.jpg`;
}

/** Draws the bitmap at `width` × `height` on a white background and encodes it as JPEG. */
async function encodeJpeg(bitmap, width, height) {
  const draw = (context) => {
    // A transparent PNG or WebP would turn black in a JPEG.
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(bitmap, 0, 0, width, height);
  };
  if (typeof OffscreenCanvas === "function") {
    try {
      const canvas = new OffscreenCanvas(width, height);
      const context = canvas.getContext("2d");
      if (context && typeof canvas.convertToBlob === "function") {
        draw(context);
        return await canvas.convertToBlob({ type: "image/jpeg", quality: JPEG_QUALITY });
      }
    } catch {
      // Fall through to a regular canvas.
    }
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return null;
  draw(context);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
}

/**
 * The file to upload: a downsized JPEG `File` when the photo is over the thresholds,
 * otherwise (or on any failure) `file` itself. Never rejects.
 */
export async function preparePhoto(file) {
  if (typeof createImageBitmap !== "function") return file;
  let bitmap = null;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const longSide = Math.max(bitmap.width, bitmap.height);
    if (!longSide) return file;
    // jpeg, png and webp under the thresholds go up as they are; any other type the
    // browser can decode (HEIC on Safari, BMP, GIF) is always converted, so the result
    // doesn't depend on its size (found by /code-review).
    const accepted = ACCEPTED_TYPES.has(file.type);
    if (accepted && file.size <= RESIZE_ABOVE_BYTES && longSide <= MAX_LONG_SIDE) return file;
    const scale = Math.min(1, MAX_LONG_SIDE / longSide);
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const blob = await encodeJpeg(bitmap, width, height);
    if (!blob || !blob.size) return file;
    // Re-encoding a photo that needed no scaling can come out bigger; keep the original then.
    if (accepted && scale === 1 && blob.size >= file.size) return file;
    return new File([blob], jpegName(file.name), {
      type: "image/jpeg",
      lastModified: file.lastModified,
    });
  } catch {
    return file;
  } finally {
    if (bitmap && typeof bitmap.close === "function") bitmap.close();
  }
}
