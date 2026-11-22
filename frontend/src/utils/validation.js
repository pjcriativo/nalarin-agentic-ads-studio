// Validation utilities for user inputs
import i18n from '../i18n';

const tv = (key, opts) => i18n.t(`validation:${key}`, opts);

/**
 * Validates and sanitizes a brand name
 * @param {string} name - The brand name to validate
 * @returns {string} - The sanitized brand name
 * @throws {Error} - If validation fails
 */
export const validateBrandName = (name) => {
    if (!name || name.trim().length === 0) {
        throw new Error(tv('brandNameRequired'));
    }
    if (name.length > 100) {
        throw new Error(tv('brandNameMax'));
    }
    return name.trim();
};

/**
 * Validates a hex color code
 * @param {string} color - The hex color to validate
 * @returns {string} - The validated color
 * @throws {Error} - If validation fails
 */
export const validateHexColor = (color) => {
    const hexRegex = /^#[0-9A-Fa-f]{6}$/;
    if (!hexRegex.test(color)) {
        throw new Error(tv('hexColor'));
    }
    return color;
};

/**
 * Validates product name
 * @param {string} name - The product name to validate
 * @returns {string} - The sanitized product name
 * @throws {Error} - If validation fails
 */
export const validateProductName = (name) => {
    if (!name || name.trim().length === 0) {
        throw new Error(tv('productNameRequired'));
    }
    if (name.length > 100) {
        throw new Error(tv('productNameMax'));
    }
    return name.trim();
};

/**
 * Validates product description
 * @param {string} description - The product description to validate
 * @returns {string} - The sanitized description
 */
export const validateProductDescription = (description) => {
    if (description && description.length > 500) {
        throw new Error(tv('productDescriptionMax'));
    }
    return description.trim();
};

/**
 * Validates brand voice/tone
 * @param {string} voice - The brand voice to validate
 * @returns {string} - The sanitized voice
 */
export const validateBrandVoice = (voice) => {
    if (voice && voice.length > 500) {
        throw new Error(tv('brandVoiceMax'));
    }
    return voice.trim();
};

/**
 * Validates text input with configurable max length
 * @param {string} text - The text to validate
 * @param {number} maxLength - Maximum allowed length
 * @param {string} fieldName - Name of the field for error messages
 * @returns {string} - The sanitized text
 */
export const validateTextInput = (text, maxLength, fieldName = i18n.t('validation:defaultField')) => {
    if (text && text.length > maxLength) {
        throw new Error(tv('textMax', { field: fieldName, max: maxLength }));
    }
    return text.trim();
};
