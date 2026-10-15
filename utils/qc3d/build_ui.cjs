// Copyright (C) CVAT.ai Corporation
// SPDX-License-Identifier: MIT

// Run from the repository root in a build environment with installed dependencies:
// node --max-old-space-size=1800 utils/qc3d/build_ui.cjs
// Serial minification avoids spawning one memory-heavy worker per CPU on small laptops.
const path = require('path');
const webpack = require('webpack');
const TerserPlugin = require('terser-webpack-plugin');

const root = path.resolve(__dirname, '../..');
process.chdir(path.join(root, 'cvat-ui'));
const config = require(path.join(root, 'cvat-ui/webpack.config.js'))({}, { mode: 'production' });
config.cache = {
    type: 'filesystem',
    cacheDirectory: path.join(root, 'node_modules/.cache/qc3d-webpack'),
    buildDependencies: { config: [__filename, path.join(root, 'cvat-ui/webpack.config.js')] },
};
config.optimization = {
    ...config.optimization,
    minimizer: [new TerserPlugin({ parallel: false })],
};
webpack(config, (error, stats) => {
    if (error) {
        console.error(error);
        process.exitCode = 1;
        return;
    }
    console.log(stats.toString({ preset: 'errors-warnings', timings: true }));
    process.exitCode = stats.hasErrors() ? 1 : 0;
});
