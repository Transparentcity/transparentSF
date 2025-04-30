import express from 'express';
import bodyParser from 'body-parser';
import dotenv from 'dotenv';
import GhostAdminAPI from '@tryghost/admin-api';
import path from 'path';
import tmp from 'tmp';
import * as fs from 'fs/promises';
import { fileURLToPath } from 'url';
import { dirname } from 'path';
import * as cheerio from 'cheerio';
import { existsSync } from 'fs';

// Define __filename and __dirname for ES modules
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Clear any existing Ghost-related environment variables
delete process.env.GHOST_URL;
delete process.env.GHOST_ADMIN_API_KEY;
delete process.env.REPLIT_GHOST_URL;
delete process.env.REPLIT_GHOST_API_KEY;

// Load .env file with override option
dotenv.config({ 
    path: path.join(__dirname, '../../.env'),
    override: true 
});

// Immediately store the values we want
const GHOST_CONFIG = {
    url: process.env.GHOST_URL,
    adminApiKey: process.env.GHOST_ADMIN_API_KEY
};


console.log('Using Ghost configuration:', {
    url: GHOST_CONFIG.url,
    keyPreview: GHOST_CONFIG.adminApiKey ? `${GHOST_CONFIG.adminApiKey.substring(0, 8)}...` : 'not set'
});

// After loading the .env file but before creating the API instance
const validateGhostApiKey = (key) => {
    if (!key) return false;
    const parts = key.split(':');
    return parts.length === 2 && parts[0].length > 0 && parts[1].length > 0;
};

// Validate configuration
if (!GHOST_CONFIG.url || !GHOST_CONFIG.adminApiKey) {
    throw new Error('Ghost configuration is missing. Please check your .env file.');
}

if (!validateGhostApiKey(GHOST_CONFIG.adminApiKey)) {
    throw new Error('Invalid Ghost API key format. Expected format: {id}:{secret}');
}

// Add more detailed error handling in the API initialization
const api = new GhostAdminAPI({
    url: GHOST_CONFIG.url.replace(/\/$/, ''), // Just remove trailing slash
    key: GHOST_CONFIG.adminApiKey,
    version: 'v5.0'
});

// Force use of .env file values by temporarily storing Replit values
const replitGhostUrl = process.env.GHOST_URL;
const replitGhostApiKey = process.env.GHOST_API_KEY;

// Log final configuration being used
console.log('Final Ghost Configuration:', {
    url: process.env.GHOST_URL,
    apiKeyPreview: process.env.GHOST_ADMIN_API_KEY ? 
        `${process.env.GHOST_ADMIN_API_KEY.substring(0, 8)}...` : 'not set'
});

// Modify the validateGhostConnection function to test different URL formats
const validateGhostConnection = async () => {
    try {
        console.log('Attempting to connect to Ghost...');
        const response = await api.site.read();
        console.log('Successfully connected to Ghost instance at:', response.url);
        return true;
    } catch (error) {
        console.error('Failed to connect to Ghost:', {
            status: error.status,
            code: error.code,
            message: error.message,
            details: error.response?.data || 'No additional details',
            configuredUrl: GHOST_CONFIG.url,
            apiKeyFormat: validateGhostApiKey(GHOST_CONFIG.adminApiKey) ? 'valid' : 'invalid'
        });
        
        // Try different URL formats
        const urlFormats = [
            `${GHOST_CONFIG.url}/ghost/api/v5/admin/site/`,
            `${GHOST_CONFIG.url}/ghost/api/admin/site/`,
            `${GHOST_CONFIG.url}/ghost/api/v5/admin/`,
            `${GHOST_CONFIG.url}/ghost/api/admin/`
        ];

        console.log('Trying different URL formats...');
        
        for (const testUrl of urlFormats) {
            try {
                console.log(`Testing URL: ${testUrl}`);
                const testResponse = await fetch(testUrl, {
                    headers: {
                        'Authorization': `Ghost ${GHOST_CONFIG.adminApiKey}`
                    }
                });
                console.log(`Response for ${testUrl}:`, {
                    status: testResponse.status,
                    statusText: testResponse.statusText
                });
                if (testResponse.ok) {
                    console.log('Found working URL format:', testUrl);
                    break;
                }
            } catch (fetchError) {
                console.error(`Failed testing ${testUrl}:`, fetchError.message);
            }
        }
        
        return false;
    }
};

const app = express();
app.use(bodyParser.json());

/**
 * Uploads an image to Ghost and returns the image URL.
 *
 * @param {Buffer} imageBuffer - The image data as a buffer.
 * @param {string} imageName - The name to assign to the uploaded image.
 * @param {GhostAdminAPI} api - The initialized GhostAdminAPI instance.
 * @returns {string} - The URL of the uploaded image.
 */
const uploadImageToGhost = async (imageBuffer, imageName, api) => {
    const tempFile = tmp.fileSync({ postfix: path.extname(imageName) });
    const tempPath = tempFile.name;

    try {
        // Write the image buffer to the temporary file
        await fs.writeFile(tempPath, imageBuffer);
        console.log(`Image written to temporary path: ${tempPath}`);

        // Upload the image using the file path
        const response = await api.images.upload({
            file: tempPath,
            name: imageName
        });

        console.log(`Image successfully uploaded to Ghost: ${response.url}`);
        return response.url;
    } catch (error) {
        console.error('Error uploading image to Ghost:', error);
        throw error;
    } finally {
        tempFile.removeCallback();
        console.log(`Temporary image file deleted: ${tempPath}`);
    }
};

app.post('/create-post', async (req, res) => {
    try {
        console.log("Request received for /create-post"); 
        const { title, content } = req.body; 

        if (!title || !content) {
            console.error("Missing title or content in request body.");
            return res.status(400).json({ success: false, error: "Title and content are required." });
        }

        // Load the HTML content into cheerio
        const $ = cheerio.load(content);

        // --- Image Upload Logic --- 
        // Select only images with src starting with 'charts/'
        const imgTags = $('img[src^="charts/"]'); 
        console.log(`Found ${imgTags.length} local chart image(s) to upload.`);

        // Define the base path to the reports directory relative to this script
        // ghostBridge.js is in ai/tools/ghost_bridge/, reports are in ai/output/reports/
        const reportsBasePath = path.resolve(__dirname, '..', '..', 'output', 'reports');
        console.log(`Reports base path resolved to: ${reportsBasePath}`);

        // Use Promise.all to handle uploads concurrently (optional, but can speed things up)
        const uploadPromises = [];

        imgTags.each((index, imgElement) => {
            const img = $(imgElement);
            const originalSrc = img.attr('src');
            
            if (originalSrc && originalSrc.startsWith('charts/')) {
                console.log(`Processing image source: ${originalSrc}`);

                // Construct the full local path to the image file
                const imagePath = path.join(reportsBasePath, originalSrc);
                const imageName = path.basename(imagePath);
                console.log(`Attempting to read image file at: ${imagePath}`);

                // Create a promise for each image processing task
                const uploadPromise = (async () => {
                    try {
                        // Check if the file exists using fs.promises.access
                        await fs.access(imagePath);
                        console.log(`Image file exists at: ${imagePath}`);

                        // Read the image file
                        const imageBuffer = await fs.readFile(imagePath);
                        console.log(`Read image file: ${imageName}, size: ${imageBuffer.length} bytes`);

                        // Upload to Ghost and get the new URL
                        const imageUrl = await uploadImageToGhost(imageBuffer, imageName, api);
                        console.log(`Image successfully uploaded. Ghost URL: ${imageUrl}`);

                        // Replace the src attribute with the new URL
                        img.attr('src', imageUrl);
                        console.log(`Replaced local src "${originalSrc}" with Ghost URL: ${imageUrl}`);

                    } catch (error) {
                        if (error.code === 'ENOENT') {
                            console.error(`Image file not found at ${imagePath}. Skipping upload.`);
                        } else {
                            console.error(`Error processing image ${imagePath}:`, error.message);
                            // Optionally decide how to handle other errors (e.g., remove the img tag, leave src as is)
                        }
                        // Don't re-throw here, allow other images to process
                    }
                })();
                uploadPromises.push(uploadPromise);
            }
        });

        // Wait for all image uploads and replacements to complete
        await Promise.all(uploadPromises);
        console.log("Finished processing all local images.");
        // --- End Image Upload Logic ---

        // Get the updated HTML content after all replacements
        const updatedContent = $.html();
        // Optional: Log a snippet of the updated HTML for verification
        // console.log("Updated HTML snippet:", updatedContent.substring(0, 500)); 

        // Create the new post in Ghost using the updated HTML
        console.log(`Creating Ghost post with title: "${title}"`);
        const newPost = await api.posts.add({
            title: title,
            html: updatedContent, 
            status: 'draft' 
        }, {
            source: 'html' 
        });

        console.log(`Ghost post created successfully: ${newPost.url}`);
        res.json({ success: true, post: newPost }); 
    } catch (error) {
        // Log the detailed error, including stack trace if available
        console.error("Error creating post:", error.message, error.stack);
        // Provide a more informative error response
        res.status(500).json({ 
            success: false, 
            error: error.message, 
            details: error.cause || error.stack // Include cause or stack if available
        });
    }
});

// Add this new endpoint for testing
app.get('/test-connection', async (req, res) => {
    try {
        const site = await api.site.read();
        res.json({
            success: true,
            connectedTo: site.url,
            title: site.title
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            error: error.message,
            configuredUrl: GHOST_CONFIG.url
        });
    }
});

// Add a health check endpoint
app.get('/check-ghost', async (req, res) => {
    try {
        // Try to fetch the Ghost site without authentication first
        const siteResponse = await fetch(GHOST_CONFIG.url);
        const ghostHealth = {
            siteAccessible: siteResponse.ok,
            siteStatus: siteResponse.status,
            adminUrl: `${GHOST_CONFIG.url}/ghost`,
            apiUrl: `${GHOST_CONFIG.url}/ghost/api/v5/admin/site/`
        };
        
        res.json({
            success: true,
            health: ghostHealth,
            config: {
                url: GHOST_CONFIG.url,
                apiKeyValid: validateGhostApiKey(GHOST_CONFIG.adminApiKey)
            }
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            error: error.message,
            config: {
                url: GHOST_CONFIG.url,
                apiKeyValid: validateGhostApiKey(GHOST_CONFIG.adminApiKey)
            }
        });
    }
});

// Add a simple endpoint to check if the Ghost site is accessible
app.get('/check-site', async (req, res) => {
    try {
        // First try accessing the main site
        const mainResponse = await fetch(GHOST_CONFIG.url);
        const ghostResponse = await fetch(`${GHOST_CONFIG.url}/ghost/`);
        
        res.json({
            success: true,
            mainSite: {
                status: mainResponse.status,
                ok: mainResponse.ok
            },
            ghostAdmin: {
                status: ghostResponse.status,
                ok: ghostResponse.ok
            },
            url: GHOST_CONFIG.url
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            error: error.message,
            url: GHOST_CONFIG.url
        });
    }
});

const PORT = process.env.PORT || 3000;

// Call the validation when the server starts
app.listen(PORT, async () => {
    console.log(`Ghost bridge service running on port ${PORT}`);
    await validateGhostConnection();
});
