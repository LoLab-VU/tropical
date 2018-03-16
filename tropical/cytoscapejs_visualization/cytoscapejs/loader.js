if (window['cytoscape'] === undefined) {
    console.log('starting loading');
    requirejs.config({

        paths: {
            'cytoscape': 'https://cdnjs.cloudflare.com/ajax/libs/cytoscape/2.7.23/cytoscape.min',
            'cytoscape-qtip': 'https://cdn.rawgit.com/cytoscape/cytoscape.js-qtip/2.7.0/cytoscape-qtip',
            'cytoscape-popper': 'https://cdn.rawgit.com/cytoscape/cytoscape.js-popper/3ad50859/cytoscape-popper',
			'jquery': 'https://cdnjs.cloudflare.com/ajax/libs/jquery/2.2.4/jquery.min',
            'qtip2': 'https://cdnjs.cloudflare.com/ajax/libs/qtip2/2.2.0/basic/jquery.qtip.min',
            'popper': 'https://cdnjs.cloudflare.com/ajax/libs/popper.js/1.14.0/umd/popper'
        },
        shim: {
            'cytoscape-popper':{
                deps: ['cytoscape', 'popper']
            }
        }
    });
    window.$ = window.jQuery = require('jquery');

    requirejs(['cytoscape', 'cytoscape-qtip', 'cytoscape-popper', 'popper', 'jquery', 'qtip2'],
        function (cytoscape, cyqtip, cypopper, popper, jquery) {
            console.log('Loading Cytoscape.js Module...');
            cyqtip(cytoscape, jquery);
            cypopper(cytoscape);
            window['cytoscape'] = cytoscape;

            var event = document.createEvent("HTMLEvents");
            event.initEvent("load_cytoscape", true, false);
            window.dispatchEvent(event);

    });

}