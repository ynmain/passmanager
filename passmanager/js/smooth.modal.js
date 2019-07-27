/*!
 * smooth.modal.js
 * Original author: @Yosuke Nakayama
 * Licensed under the MIT license
*/



$(function(){

	$.fn.modalWindow = function (options) {

        options = $.extend({
			modalWidth : 800,
			modalHeight : 420,
			fadeSpeed:200
        }, options);
		
		var height = $($("body")).height(),
		get_modal_position = 0;
		$("body")
		.prepend('<div id="gyLayer"></div><div id="overLayer"><div class="close"><img src="images/btn_close.png" width="29" height="29" alt=""></div></div>');
		
		
		$("#gyLayer").css({
			"position":"absolute",
			width: "100%",
			height:"100%",
			background:"#000",
			"-ms-filter": "alpha(opacity=50)" ,
			"filter": "alpha(opacity=50)",
			"opacity":0.50,
			left: 0,
			top: 0,
			"z-index":100,
			"display":"none"
		})
		
		$("#overLayer").css({
			"top": "120",
			"left": "50%",
			"width":"800px",
			"height":"400px",
			"margin-left": "-400px",
			"position": "absolute",
			"z-index":"101",
			"display":"none"
		}).find(".close").css({
			
			"position": "absolute",
			"right": "-55px",
			top : 0,
			"cursor":"pointer"
			
		});
		
		
		
	$("#gyLayer").height(height);
	
		$(window).scroll(function(){
			get_modal_position = $(this).scrollTop();
		});
		
        this.each(function () {
			
			function modalShow(){
		
				$("#gyLayer:not(:animated)").stop().fadeIn(options.fadeSpeed,function(){
					$("#overLayer:not(:animated)").stop().show().css({"top":-500}).animate({
						marginTop:get_modal_position,
						top:"13%"
					},700,"easeOutBack");		
				});	
				
				$("#gyLayer,.close").click(function(){
					$("#overLayer:not(:animated)").stop().animate({
							top:get_modal_position+150,
							opacity:0
						},300,"easeInExpo",function(){
							$(this).hide();
							$("#gyLayer:not(:animated)").stop().fadeOut(300,function(){
								
							$("#overLayer:not(:animated)").css({"opacity":1});
							
							$(".modal").remove();
							
				});
			});	
		})
	}
			
		$(this).on("click",function(){
			
			src = String($(this).find("a").attr("href"));
			$("#overLayer").prepend('<iframe class="modal" src="'+src.slice(1)+'" width="'+options.modalWidth+'" height="'+options.modalHeight+'" border="0" scrolling="no" ></iframe>');

			modalShow();
		});
		
	
		});
		return this;
    };
});