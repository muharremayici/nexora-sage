'use strict';
module.exports = Object.freeze({
    version: '0.0.0-synthetic',
    parseConfigFileTextToJson(filename, text) { return {config: JSON.parse(text)}; },
    preProcessFile() { return {typeReferenceDirectives: [], importedFiles: [], referencedFiles: []}; },
    createProgram() { throw new Error('reviewed fixture Program forbidden'); }
});
