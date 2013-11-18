_.templateSettings = {
  interpolate: /\{\{(.+?)\}\}/g
};

function disableSelection(element) {
    if (typeof element.onselectstart != 'undefined') {
        element.onselectstart = function() { return false; };
    } else if (typeof element.style.MozUserSelect != 'undefined') {
        element.style.MozUserSelect = 'none';
    } else {
        element.onmousedown = function() { return false; };
    }
}

var Message = Backbone.Model.extend({});
var StatusMessage = Backbone.Model.extend({});

var MessageList = Backbone.Collection.extend({
    model: Message
});

var Line = Backbone.View.extend({
    className: 'line',
    tagName: 'tr',
    events: {},
    template: _.template('<td class="tag">{{ tag }}</td><td class="level"><span class="{{ level }}">{{ level }}</span></td><td class="message">{{ message }}</td>'),
    render: function() {
        this.$el.html(this.template(this.model.attributes));
        return this;
    }
});

var StatusLine = Backbone.View.extend({
    className: 'line status-line',
    tagName: 'tr',
    events: {},
    template: _.template('<td colspan="3">{{ status }}</td>'),
    render: function() {
        this.$el.html(this.template(this.model.attributes));
        return this;
    }
});

var Console = Backbone.View.extend({
    className: "console",
    events: {
        'mousedown .title': 'startDragging',
        'mouseup': 'stopDragging',
        'mousemove': 'drag'

    },

    initialize: function() {
        this.listenTo(this.collection, "add", this.addLine);
    },

    template: _.template('<div class="title">{{ title }}</div><div class="content"><table></table></div>'),

    append: function(line) {
        this.$('.content table').append(line.render().$el);
        this.$('.content').scrollTop(this.$('.content').get(0).scrollHeight);
    },

    addLine: function(line) {
        var line = new Line({model: line});
        this.append(line);
    },

    setStatus: function(status) {
        var statusLine = new StatusLine({model: status});
        this.append(statusLine);
    },

    render: function() {
        this.$el.html(this.template({'title': 'logcat'}));
        return this;
    },

    startDragging: function(e) {
        this.$el.addClass('dragging');
        this.dragging = true;
        this.downX = e.pageX;
        this.downY = e.pageY;
    },

    stopDragging: function(e) {
        this.$el.removeClass('dragging');
        this.dragging = false;
    },

    drag: function(e) {
        if (this.dragging) {

            var deltaX = e.pageX - this.downX;
            var deltaY = e.pageY - this.downY;
            var offset = this.$el.offset();

            this.$el.offset({
                top: offset.top + deltaY,
                left: offset.left + deltaX
            });

            this.downX = e.pageX;
            this.downY = e.pageY;
        }
    }

});

function reconnect(view) {
    window.setTimeout(function() {
        console.log("reconnecting");
        connect(view);
    },500);
}

function connect(view) {
    var socket = new WebSocket("ws://localhost:8000/logcat");

    socket.onopen = function() {
        console.log('socket opened', arguments);
    };

    socket.onclose = function() {
        console.log('socket closed', arguments);
    };

    socket.onerror = function() {
        console.log('socket error!', arguments);
        reconnect(view);
    };

    socket.onmessage = function(e) {
        var msg = JSON.parse(e.data);

        console.log('message', msg);

        if (msg.type == 'log')
            view.collection.add(msg.payload);
        if (msg.type == 'status')
            view.setStatus(new StatusMessage(msg.payload));

    };
}

function createConsole() {
    var messages = new MessageList();
    var message_console = new Console({collection: messages});
    message_console.render();

    $('body').append(message_console.$el);

    connect(message_console);
}

$(function() {

    disableSelection(document.body);

    $('#new-console').click(function() {
        createConsole();
        return false;
    });

    createConsole();

});
