/*!
 * text.layer.animation.js
 * Original author: @Yosuke Nakayama
 * Licensed under the MIT license
 */

$(function () {
	
    $.fn.textLayerAnimation = function (options) {
        options = $.extend({
			speed:800,
			easing:"easeOutExpo"
        }, options);
		
		var _self = $(this);
		var textSize = $(this).width();
		
		$(this).css({
			"overflow": "hidden",
			"opacity" : 0.1,
			"white-space": "nowrap"
		});
		
		$(this).wrap("<div class='textLayerAnimationArea' />");
		
		$(".textLayerAnimationArea").css({
			"position": "relative"	
		});
		
        $(".textLayerAnimationArea").each(function(){
			$(this).append('<p class="textMaskAfter">'+$(this).find(_self).text()+'</p>');
		})
		
		$(".textMaskAfter").css({
			
			"position":"absolute",
			"top": 0,
			"width":0,
			"overflow": "hidden",
			"white-space": "nowrap"	
		})
			.each(function(i){
				$(".textMaskAfter").eq(i).delay(i*100).transition({"width":textSize},options.speed,options.easing);
			});
			
        return this;
    };
	
});
