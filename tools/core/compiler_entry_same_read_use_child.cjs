'use strict';
// Fixed source-selected endpoint; clock includes protocol and schema loading.
const started=require('node:perf_hooks').performance.now();
require('./compiler_entry_fixed_use_child.cjs').runSynthetic(started,
    require('../../config/schemas/compiler_entry_same_read_use_transport.schema.json'),
    require('../../config/schemas/compiler_entry_identity.schema.json'),
    require('../../config/schemas/compiler_entry_owner.schema.json'),
    require('../../config/schemas/compiler_entry_same_read_use.schema.json'));
