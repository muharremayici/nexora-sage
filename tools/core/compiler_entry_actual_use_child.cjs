'use strict';
// Fixed source-selected endpoint; clock includes protocol and schema loading.
const started=require('node:perf_hooks').performance.now();
require('./compiler_entry_fixed_use_child.cjs').runHistorical(started,
    require('../../config/schemas/compiler_entry_actual_use_transport.schema.json'),
    require('../../config/schemas/compiler_entry_actual_identity.schema.json'),
    require('../../config/schemas/compiler_entry_owner.schema.json'),
    require('../../config/schemas/compiler_entry_actual_use.schema.json'));
