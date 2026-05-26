import subprocess
from pathlib import Path


BUSINESSES = {
    "riverside_grocery": {
        "color": "0x3a7d44",
        "respondents": {
            "sarah_k": {
                "duration": 220,
                "transcript": """\
[0:00] Speaker: Hi, I'm Sarah. I've been shopping at Riverside Market for about three years now.
[0:06] Speaker: How was your experience with the checkout process today?
[0:10] Speaker: The checkout lines were really long today, I have to say.
[0:15] Speaker: I waited about fifteen minutes just to get through the express lane.
[0:20] Speaker: They only had two registers open during what was clearly a busy period.
[0:28] Speaker: The cashier was friendly though, so that helped.
[0:33] Speaker: How did you find the produce section?
[0:36] Speaker: The produce was excellent. Really fresh strawberries and I loved the variety of organic options.
[0:44] Speaker: The signage made it easy to tell what was local versus imported.
[0:51] Speaker: What about the staff throughout the store?
[0:55] Speaker: Staff were helpful. I asked where the tahini was and someone walked me to the aisle.
[1:02] Speaker: One thing I'll say is they really need more staff at checkout.
[1:07] Speaker: Even self-checkout had a line out to the aisle.
[1:14] Speaker: Would you come back?
[1:18] Speaker: Absolutely. The selection is great and the prices are fair. Just fix the checkout situation.
[1:25] Speaker: Is there anything else you'd like to share?
[1:29] Speaker: Just that the deli counter has gotten so much better. The turkey is sliced fresh now.
[1:37] Speaker: And honestly the parking lot is well lit at night which I appreciate.
[1:43] Speaker: But yes, my main feedback is about the checkout lines. It really slows everything down.
[1:51] Speaker: On busy days they should definitely open more registers.
[1:57] Speaker: I've started going on weekday mornings to avoid the rush.
[2:03] Speaker: But not everyone can do that.
[2:06] Speaker: Overall though I'd give the store about an eight out of ten.
[2:12] Speaker: Great products, the bakery is wonderful, I just wish checkout was faster.
[2:20] Speaker: The loyalty app also helps me save money which is a nice bonus.
[2:27] Speaker: But if I had to pick one thing to improve it would be staffing the checkout lanes.
[2:35] Speaker: During peak hours especially, they need at least four or five registers open.
[2:42] Speaker: Last week I actually left my cart and came back the next day because the line was so long.
[2:51] Speaker: That shouldn't happen.
[2:54] Speaker: But I do love the store overall. The butcher section is fantastic.
[3:01] Speaker: I've started getting all my meat there.
[3:05] Speaker: And the cheese selection has improved a lot over the past year.
[3:11] Speaker: So it's really just the checkout that brings my rating down.
[3:17] Speaker: Thank you for your time Sarah.
[3:20] Speaker: Of course. Happy to help.""",
            },
            "mike_t": {
                "duration": 260,
                "transcript": """\
[0:00] Speaker: So yeah I was in there for probably, I want to say, an hour and a half? Which is way longer than I planned.
[0:08] Speaker: Part of that was just me wandering around because they rearranged the whole store recently.
[0:15] Speaker: I couldn't find the pasta for like ten minutes.
[0:19] Speaker: But then I also got stuck in the checkout for a really long time.
[0:24] Speaker: Like, there were maybe six or seven people ahead of me and it was moving so slowly.
[0:31] Speaker: The scanner kept beeping on the person in front of me, some kind of coupon issue.
[0:38] Speaker: And the manager had to come over and it just took forever.
[0:43] Speaker: I mean I get it, things happen, but it felt like the staff weren't really trained for it.
[0:51] Speaker: The cashier seemed kind of flustered.
[0:55] Speaker: But anyway the store itself is great, I love that they carry those Japanese snacks now.
[1:02] Speaker: My kids go crazy for the Pocky.
[1:05] Speaker: Oh and the bakery — they have this sourdough that I could eat every day.
[1:12] Speaker: But yeah back to checkout, I think they really need a better system.
[1:18] Speaker: Even the self-checkout line was long.
[1:22] Speaker: I tried switching over but there were four people waiting there too.
[1:27] Speaker: And then one of the self-checkout machines wasn't working.
[1:32] Speaker: So that just made everything worse.
[1:36] Speaker: I think it was about a twenty minute wait total for me.
[1:41] Speaker: Which honestly for a grocery store is a lot.
[1:45] Speaker: Like I'm not trying to spend my whole Saturday there, you know?
[1:50] Speaker: But I did find everything I needed which was good.
[1:54] Speaker: Oh and there was a sample station near the deli that was giving out little cups of soup.
[2:00] Speaker: That was a nice touch.
[2:03] Speaker: The soup was really good actually — some kind of butternut squash.
[2:09] Speaker: I might have gone back for seconds.
[2:12] Speaker: But yeah the main thing I want to say is checkout. Please fix checkout.
[2:18] Speaker: Open more registers. Maybe have someone dedicated to helping with the self-checkout machines.
[2:26] Speaker: I've been going there for years and the checkout has always been a weak point.
[2:32] Speaker: Everything else is great though. Really great produce.
[2:37] Speaker: I got these beautiful heirloom tomatoes last week.
[2:41] Speaker: And the fish counter has really improved.
[2:45] Speaker: They now have someone there full time on weekends.
[2:49] Speaker: Last time I went I got this beautiful piece of halibut.
[2:54] Speaker: Okay I'm getting sidetracked.
[2:57] Speaker: The point is: great store, bad checkout experience, please fix it.
[3:04] Speaker: Especially on weekends. Saturdays between noon and four are brutal.
[3:11] Speaker: You'd think they'd know to staff up then.
[3:15] Speaker: Like look at the data, see when it's busy, put more people at the registers.
[3:21] Speaker: It's not complicated.
[3:24] Speaker: Anyway that's my main feedback.
[3:27] Speaker: Oh wait I also wanted to mention the parking.
[3:31] Speaker: The parking lot is a little chaotic but I've gotten used to it.
[3:36] Speaker: Nothing too bad.
[3:38] Speaker: And the prices are competitive for the quality.
[3:43] Speaker: I compared a few things to the store across town and Riverside is usually a bit cheaper.
[3:50] Speaker: Or at least comparable.
[3:52] Speaker: So yeah. Good store, fix the checkout, that's my review.
[3:59] Speaker: Thanks.""",
            },
            "diana_p": {
                "duration": 160,
                "transcript": """\
[0:00] Speaker: Hello, I'm Diana. I shop at Riverside Market about once a week.
[0:06] Speaker: How was your visit today overall?
[0:09] Speaker: Really pleasant, thank you. I found everything on my list and the store was clean.
[0:15] Speaker: What stood out to you most?
[0:18] Speaker: The fresh flowers at the entrance. They had the most beautiful sunflowers and dahlias today.
[0:25] Speaker: I ended up buying a bouquet, which I wasn't planning on.
[0:30] Speaker: How was the produce section?
[0:33] Speaker: Wonderful as always. The organic section has really expanded.
[0:38] Speaker: I got peaches and they smell incredible.
[0:42] Speaker: Very good variety and everything looked fresh.
[0:47] Speaker: Any issues during your visit?
[0:50] Speaker: Not really. The checkout line moved a bit slow today but it wasn't a big deal.
[0:57] Speaker: I was in no rush so I didn't mind.
[1:01] Speaker: How do you feel about the staff?
[1:04] Speaker: Very friendly and knowledgeable. I asked about a specific type of rice and they knew exactly where it was.
[1:12] Speaker: And when I couldn't reach something on the top shelf someone immediately helped me.
[1:19] Speaker: Would you recommend Riverside Market to others?
[1:23] Speaker: Absolutely without hesitation. It's my favorite grocery store in the area.
[1:30] Speaker: The quality is consistently high and I feel good about where the products come from.
[1:37] Speaker: Is there anything you'd like to see improved?
[1:40] Speaker: Maybe a few more international food options, I'm always looking for specific Thai ingredients.
[1:48] Speaker: And perhaps more electric vehicle charging spots in the parking lot.
[1:53] Speaker: But those are minor things. The store is excellent as is.
[2:00] Speaker: Do you use the store's loyalty program?
[2:03] Speaker: Yes, I love it. I saved almost forty dollars last month just on weekly specials.
[2:10] Speaker: The app is easy to use too. I clip my coupons on the way to the store.
[2:17] Speaker: Great. Thank you so much Diana.
[2:20] Speaker: My pleasure. Keep up the great work, Riverside Market.""",
            },
            "james_r": {
                "duration": 178,
                "transcript": """\
[0:00] Speaker: Look, I'll be honest. I wasn't sure I was going to keep coming here after the price increase last fall.
[0:08] Speaker: Everything went up. The coffee especially. I used to get this Ethiopian blend for six ninety-nine.
[0:15] Speaker: Now it's nine forty-nine. That's a big jump.
[0:19] Speaker: But I went back because the quality is honestly just better than the places nearby.
[0:27] Speaker: And you know what, the parking situation has gotten a lot better.
[0:32] Speaker: They redid the whole lot and added about thirty more spaces.
[0:37] Speaker: There's also a clear lane for pickup orders now so that doesn't block traffic anymore.
[0:44] Speaker: I really appreciate that.
[0:47] Speaker: The online ordering is great too. I've started doing a mix of pickup and in-store.
[0:54] Speaker: The pickup orders are always accurate.
[0:57] Speaker: Well once they gave me two percent instead of whole milk but that's one time in like a year.
[1:04] Speaker: Overall the store is just really well organized.
[1:09] Speaker: I can get in and out pretty quickly when I need to.
[1:13] Speaker: The layout makes sense.
[1:16] Speaker: I do wish they'd bring back the hot bar. They had this amazing mac and cheese.
[1:22] Speaker: They removed it during the pandemic and never brought it back.
[1:27] Speaker: A lot of people I know miss it.
[1:30] Speaker: But the deli is still great, so that helps.
[1:34] Speaker: And the bakery goods. Oh man, their croissants on Sunday mornings.
[1:40] Speaker: I drive past two other grocery stores just to get those.
[1:45] Speaker: Anyway, my main feedback is on pricing.
[1:49] Speaker: I think they could be a bit more competitive on everyday staples.
[1:54] Speaker: Specialty items, fine, charge a premium.
[1:58] Speaker: But milk and eggs and bread shouldn't cost thirty percent more than the big chains.
[2:06] Speaker: That's where I think they lose some customers.
[2:11] Speaker: I can afford it but plenty of people in the neighborhood can't.
[2:17] Speaker: And I'd hate to see this place become only for wealthy shoppers.
[2:23] Speaker: It used to feel more accessible.
[2:27] Speaker: But again, parking is great now, store is clean, products are excellent.
[2:34] Speaker: Just watch the pricing on essentials.
[2:38] Speaker: That's my main thing.""",
            },
            "lynn_b": {
                "duration": 223,
                "transcript": """\
[0:00] Speaker: So my experience today — I'd say it was mostly good with one really frustrating part.
[0:07] Speaker: And that part was checkout.
[0:10] Speaker: How long did you wait?
[0:12] Speaker: Almost twenty-five minutes. For a grocery store that's just unacceptable to me.
[0:18] Speaker: I counted four people behind me when I finally got through, so it wasn't just me.
[0:25] Speaker: There were three lanes open when there are clearly twelve registers.
[0:31] Speaker: What was the experience like at the register itself?
[0:35] Speaker: Once I got there it was fine. The cashier was efficient and friendly.
[0:40] Speaker: She asked if I found everything, helped me bag the fragile stuff.
[0:46] Speaker: No complaints about the actual transaction.
[0:50] Speaker: Just the waiting.
[0:53] Speaker: Right so the issue is really the number of lanes open.
[0:58] Speaker: Exactly. They need to match staffing to customer volume.
[1:04] Speaker: On a Sunday afternoon I'd expect at least six or seven lanes open.
[1:11] Speaker: And you know what, the self checkout was out of order.
[1:16] Speaker: Well, two of the four machines were out of order.
[1:20] Speaker: So everyone was getting funneled into fewer options.
[1:25] Speaker: Was there anything positive about today?
[1:28] Speaker: Oh definitely. The store looked great. Really clean and well-stocked.
[1:34] Speaker: I found some specialty items I'd been looking for for months.
[1:39] Speaker: They had a Korean chili paste I've been hunting for everywhere.
[1:44] Speaker: And the cheese section is incredible. I spent way too long there.
[1:50] Speaker: I ended up with three cheeses I hadn't planned on buying.
[1:55] Speaker: That happens every time honestly.
[1:59] Speaker: Ha. What would be your overall rating?
[2:03] Speaker: Seven out of ten today, mostly because of checkout. Normally I'd say nine.
[2:09] Speaker: The store itself is wonderful. Seriously.
[2:14] Speaker: Amazing butcher, fantastic bakery, really thoughtful product selection.
[2:20] Speaker: I love that they stock local brands.
[2:23] Speaker: I bought this hot sauce from a company two towns over that I'd never heard of and it's incredible.
[2:31] Speaker: So the product curation is excellent.
[2:35] Speaker: I just wish the operational side — particularly checkout staffing — matched the quality of the merchandise.
[2:44] Speaker: It's the main reason I don't come here every single week.
[2:49] Speaker: Sometimes I go to the bigger chain just because I know I'll be in and out in ten minutes.
[2:57] Speaker: Which is a shame because this store is genuinely better in almost every other way.
[3:05] Speaker: They just need to solve the checkout problem.
[3:09] Speaker: More registers open, more consistent staffing, get those self-checkout machines fixed.
[3:17] Speaker: That would make a huge difference.
[3:21] Speaker: Thank you Lynn.
[3:23] Speaker: Of course. I hope the feedback is useful.""",
            },
        },
    },
    "bella_vista_restaurant": {
        "color": "0xc0392b",
        "respondents": {
            "carlos_m": {
                "duration": 152,
                "transcript": """\
[0:00] Speaker: Hi, I'm Carlos. This was my second visit to Bella Vista.
[0:05] Speaker: What brought you back for a second visit?
[0:08] Speaker: Honestly the pasta. I had the carbonara last time and couldn't stop thinking about it.
[0:15] Speaker: How was the food this time?
[0:18] Speaker: Outstanding. I had the osso buco and it was one of the best dishes I've had in this city.
[0:25] Speaker: The meat was so tender it fell off the bone. The gremolata on top was perfect.
[0:33] Speaker: My wife had the branzino and she said the same — beautifully cooked.
[0:39] Speaker: Were there any aspects of the meal you felt could improve?
[0:43] Speaker: The bread basket came out a bit cold. It felt like it had been sitting for a while.
[0:50] Speaker: For a restaurant at this level that shouldn't happen.
[0:55] Speaker: But the pasta course more than made up for it. We shared a cacio e pepe and it was exceptional.
[1:03] Speaker: How did you find the wait times?
[1:06] Speaker: We had a reservation so we were seated right away, which was great.
[1:12] Speaker: The wait between courses was reasonable. Not rushed, not too slow.
[1:18] Speaker: The pacing felt very intentional.
[1:22] Speaker: Would you describe the service?
[1:25] Speaker: Attentive without being intrusive. The sommelier made a wonderful wine recommendation.
[1:33] Speaker: We went with a Barolo and it paired beautifully with everything.
[1:39] Speaker: Any final thoughts?
[1:42] Speaker: Just that this is the best Italian restaurant I've been to outside of Italy.
[1:48] Speaker: The ingredients are clearly top quality. You can taste it.
[1:54] Speaker: The handmade pasta especially. You can taste the difference.
[2:01] Speaker: I'll be bringing clients here for sure.
[2:05] Speaker: And my mother-in-law is visiting next month and this is where I'm taking her.
[2:12] Speaker: Exceptional food. That's the bottom line.""",
            },
            "priya_s": {
                "duration": 204,
                "transcript": """\
[0:00] Speaker: I just have to start by saying, the food here is incredible.
[0:05] Speaker: Like I've been to a lot of Italian restaurants and this one is different.
[0:11] Speaker: The flavors are just deeper, more complex.
[0:15] Speaker: I don't know exactly what they're doing but whatever it is, keep doing it.
[0:21] Speaker: So we started with the burrata and it was the creamiest burrata I've ever had.
[0:28] Speaker: And I eat a lot of burrata. I'm kind of obsessed.
[0:33] Speaker: But this was something else. Served with really good olive oil and these little cherry tomatoes.
[0:40] Speaker: And then for the main I had the linguine alle vongole — the clam pasta —
[0:47] Speaker: and I just sat back and closed my eyes for a second after the first bite.
[0:52] Speaker: My friend thought I was being dramatic but I wasn't.
[0:57] Speaker: The sauce was light and briny and perfectly seasoned.
[1:03] Speaker: The pasta was cooked absolutely perfectly, al dente but not firm.
[1:09] Speaker: And there were so many clams, which, you know, is not always the case.
[1:15] Speaker: Usually you get like three sad clams at the bottom of a giant bowl of pasta.
[1:21] Speaker: Not here. It was generous and delicious.
[1:25] Speaker: We also shared a tiramisu for dessert and oh my.
[1:30] Speaker: I'm not usually a dessert person but I ate my half and then basically half of my friend's too.
[1:37] Speaker: The mascarpone was so light and the espresso flavor was really pronounced.
[1:43] Speaker: Just perfect.
[1:46] Speaker: The service was also lovely, everyone was warm and knowledgeable.
[1:52] Speaker: I asked the server about the pasta and she explained the whole process —
[1:58] Speaker: that they make it fresh every morning in house.
[2:03] Speaker: Which you can taste.
[2:06] Speaker: Oh and I should mention we had to wait about twenty minutes past our reservation time to be seated.
[2:13] Speaker: Which was a little frustrating.
[2:16] Speaker: But they brought us prosecco at the bar while we waited so that helped soften the blow.
[2:24] Speaker: Ambiance is great. Candlelit, not too loud, romantic without being stuffy.
[2:31] Speaker: The whole evening was just lovely.
[2:35] Speaker: But really what I want to say above everything is that the food is just exceptional.
[2:42] Speaker: I've already made another reservation for next month.
[2:47] Speaker: Bringing my mom this time. She's going to lose her mind over the burrata.
[2:53] Speaker: It's the kind of meal that stays with you.
[2:57] Speaker: You keep thinking about it days later. That's the mark of great cooking.
[3:04] Speaker: Five stars from me, would absolutely recommend.""",
            },
            "tom_h": {
                "duration": 122,
                "transcript": """\
[0:00] Speaker: Hi, I'm Tom. I've been wanting to try Bella Vista for a while.
[0:06] Speaker: How was your overall experience?
[0:09] Speaker: Mixed, I'd say. The service was a bit disorganized.
[0:14] Speaker: In what way?
[0:16] Speaker: We waited over forty minutes past our reservation time before being seated.
[0:22] Speaker: No one came to explain the delay or offer anything while we waited.
[0:28] Speaker: We just stood by the host stand feeling forgotten.
[0:33] Speaker: How was the service once you were seated?
[0:37] Speaker: Better, but still inconsistent. My water glass sat empty for about ten minutes at one point.
[0:44] Speaker: And we had to ask twice for the dessert menu.
[0:49] Speaker: What about the food itself?
[0:52] Speaker: The food was good. I enjoyed my rigatoni.
[0:56] Speaker: Not the most memorable meal I've ever had but solid.
[1:01] Speaker: My partner said her chicken was a bit dry.
[1:05] Speaker: Would you return?
[1:08] Speaker: Probably not, to be honest. Not at these prices with that service experience.
[1:15] Speaker: If I'm spending this much on dinner I expect a certain level of attentiveness.
[1:21] Speaker: Is there anything positive you'd highlight?
[1:24] Speaker: The ambiance is beautiful. The decor is elegant, the lighting is perfect.
[1:31] Speaker: And the wine list is impressive. Our sommelier was excellent, very knowledgeable.
[1:38] Speaker: Just wish the rest had matched that standard.
[1:42] Speaker: Thank you Tom.""",
            },
            "rachel_w": {
                "duration": 156,
                "transcript": """\
[0:00] Speaker: Okay so, starting with the most important thing — the food.
[0:05] Speaker: How was it?
[0:07] Speaker: Phenomenal. I had the gnocchi in a truffle cream sauce and it was extraordinary.
[0:13] Speaker: Each piece was pillow-soft and the sauce was rich without being overwhelming.
[0:20] Speaker: I would go back just for that dish alone.
[0:24] Speaker: And my husband had the beef tenderloin which he's still talking about two days later.
[0:31] Speaker: He said it was the best steak he's had in years.
[0:36] Speaker: Were there any highlights from earlier in the meal?
[0:40] Speaker: The calamari starter was really lovely. Lightly fried, not greasy at all.
[0:46] Speaker: Served with this interesting romesco sauce that I want to learn how to make.
[0:53] Speaker: But you know, then the conversation kind of went everywhere —
[0:58] Speaker: we were catching up with old friends we hadn't seen in a while —
[1:03] Speaker: so I wasn't paying as much attention to each dish as I might otherwise.
[1:09] Speaker: We were just having such a good time.
[1:13] Speaker: The setting really lends itself to that kind of evening.
[1:17] Speaker: Warm and intimate, you forget the rest of the world for a bit.
[1:23] Speaker: Oh but the tiramisu. Back to the food. We ordered two for the table.
[1:29] Speaker: And honestly I could have eaten a third.
[1:32] Speaker: It had this hint of orange zest that was unexpected and just worked perfectly.
[1:39] Speaker: I've had a lot of tiramisu in my life but this one had a real personality to it.
[1:46] Speaker: A real point of view, if that makes sense for a dessert.
[1:51] Speaker: Ha, sure. Final thoughts?
[1:54] Speaker: Just that the quality of the ingredients and the skill of the kitchen really shows.
[2:01] Speaker: You can tell they're not cutting corners.
[2:05] Speaker: This is the kind of Italian food I grew up eating at my grandmother's table.
[2:11] Speaker: Which is about the highest compliment I can give.
[2:16] Speaker: Really beautiful food. We'll definitely be back.""",
            },
            "david_l": {
                "duration": 154,
                "transcript": """\
[0:00] Speaker: Look, I want to be upfront that I'm not the right person to review the food because I'm not a foodie.
[0:08] Speaker: My wife is the one who picks the restaurants and she was the one who wanted to come here.
[0:15] Speaker: I just eat what's in front of me.
[0:18] Speaker: What I can tell you about is everything else.
[0:22] Speaker: The price. The price is real. This is a pricey place.
[0:27] Speaker: We spent about two hundred and forty dollars for two people with wine.
[0:33] Speaker: Now my wife says it was worth it and maybe she's right.
[0:38] Speaker: But I'm the one who looks at the bank statement so.
[0:43] Speaker: The ambiance though, I have to admit, is really something.
[0:48] Speaker: I'm not usually into the whole candles and dim lighting thing but even I appreciated it.
[0:55] Speaker: It felt like a real occasion just by being there.
[1:00] Speaker: And the bar area while we waited was very comfortable.
[1:05] Speaker: Nice bar stools, good cocktail menu, bartender was friendly and funny.
[1:12] Speaker: We ended up having two cocktails each while waiting for our table.
[1:18] Speaker: Which, I mean, also adds to the bill.
[1:22] Speaker: But we were in good spirits by the time we sat down.
[1:27] Speaker: The dining room itself is beautiful. High ceilings, nice art on the walls.
[1:34] Speaker: Feels very European.
[1:37] Speaker: Parking was a bit of a challenge. Lot nearby was full and we had to find street parking.
[1:44] Speaker: Walked about six blocks in the cold which was not ideal.
[1:50] Speaker: Maybe they should validate or have a valet option.
[1:54] Speaker: But once inside it was a lovely evening.
[1:59] Speaker: My wife was happy, which means I was happy.
[2:04] Speaker: Would we go back? She would. Me, I'd need a special occasion.
[2:10] Speaker: Or a big bonus at work. Ha.
[2:14] Speaker: But yeah, beautiful place, bring your wallet.""",
            },
        },
    },
    "iron_fitness_gym": {
        "color": "0x2c3e50",
        "respondents": {
            "alex_j": {
                "duration": 151,
                "transcript": """\
[0:00] Speaker: Hi, I'm Alex. I've been a member at Iron Fitness for about eight months.
[0:06] Speaker: How would you describe the equipment at the gym?
[0:10] Speaker: Top notch. They have a really comprehensive selection.
[0:14] Speaker: Everything from basic barbells and dumbbells up to specialized machines.
[0:20] Speaker: The cardio section is huge — probably thirty or forty machines.
[0:26] Speaker: And they're all relatively new. I haven't had a broken treadmill experience here.
[0:33] Speaker: That's been a problem at other gyms I've been to.
[0:38] Speaker: Here everything seems to be well maintained.
[0:43] Speaker: How about the free weights area?
[0:46] Speaker: Great. Full dumbbell rack up to one hundred pounds.
[0:51] Speaker: Multiple squat racks which means I can almost always get one without waiting.
[0:57] Speaker: The barbells are in good shape too. Knurling is still sharp.
[1:03] Speaker: How clean is the gym overall?
[1:06] Speaker: Very clean. Staff wipe down machines regularly throughout the day.
[1:12] Speaker: There are sanitation stations everywhere.
[1:15] Speaker: I've been to gyms that smell like a locker room everywhere.
[1:20] Speaker: This one actually smells fine.
[1:23] Speaker: What about classes?
[1:26] Speaker: I do the early morning HIIT class twice a week and it's excellent.
[1:32] Speaker: The instructor really pushes you but also watches form.
[1:37] Speaker: She corrected my squat form the first session and it made a huge difference.
[1:43] Speaker: Is there anything you'd like to see improved?
[1:47] Speaker: More cable machines would be great. Sometimes there's a wait for those.
[1:53] Speaker: And I'd love a dedicated stretching area with better mats.
[1:58] Speaker: The current mats are a bit thin.
[2:02] Speaker: But overall it's a fantastic gym. Definitely worth the membership price.
[2:11] Speaker: Thank you Alex.""",
            },
            "brittany_f": {
                "duration": 142,
                "transcript": """\
[0:00] Speaker: Okay so I joined Iron Fitness about five months ago after my old gym closed down.
[0:07] Speaker: And honestly I'm so glad they closed because this place is so much better.
[0:13] Speaker: Like the equipment alone is worth the upgrade.
[0:17] Speaker: My old gym had maybe six treadmills and half of them were always broken.
[0:23] Speaker: Here there are rows and rows of cardio equipment, all in perfect working order.
[0:29] Speaker: I tested like four different ellipticals before finding my favorite.
[0:35] Speaker: That's a luxury I did not have before.
[0:39] Speaker: And the weight section! They have this functional training area that I've really gotten into.
[0:46] Speaker: Battle ropes, TRX, kettle bells in every size.
[0:51] Speaker: I've learned more about training in five months here than in years at my last gym.
[0:58] Speaker: Partly the equipment — having the right tools really matters —
[1:03] Speaker: and partly the classes and instructors.
[1:07] Speaker: I take the spin class on Tuesdays and the yoga on Thursdays.
[1:12] Speaker: Both instructors are just phenomenal.
[1:16] Speaker: The spin instructor is this tiny woman who will absolutely destroy you in the best way.
[1:23] Speaker: And the yoga instructor is so calming and really knows her stuff anatomically.
[1:29] Speaker: Like she explains why you're holding a pose, what muscles it's activating.
[1:35] Speaker: That really helps me understand what I'm doing.
[1:39] Speaker: Um, the only thing I'll say is the locker rooms could use a little work.
[1:46] Speaker: Some of the lockers are a bit beat up and a couple of showers have weak water pressure.
[1:53] Speaker: Nothing terrible but noticeable.
[1:56] Speaker: But yeah, overall, the gym is great. The equipment is really the standout.
[2:02] Speaker: I'd recommend it to anyone who's serious about their fitness.""",
            },
            "noah_c": {
                "duration": 140,
                "transcript": """\
[0:00] Speaker: So I've been going to Iron Fitness for about a year.
[0:04] Speaker: How is your experience generally?
[0:07] Speaker: Good. I like the vibe. It's serious without being intimidating.
[0:13] Speaker: People are focused but also friendly.
[0:17] Speaker: How about the facilities?
[0:20] Speaker: Good overall. The equipment is solid, I've never had issues finding what I need.
[0:27] Speaker: Though the lat pulldown machine in the back corner has been making a weird noise lately.
[0:34] Speaker: I keep meaning to mention it to someone.
[0:38] Speaker: Other than that the machines work well and they seem to add new stuff periodically.
[0:45] Speaker: They added a new Smith machine last month which I've been using.
[0:51] Speaker: And then you know, I've been really focused lately on the sauna and steam room situation —
[0:57] Speaker: my recovery has been a huge focus for me this year —
[1:02] Speaker: and I have to say the sauna here is excellent.
[1:07] Speaker: It gets really hot and stays consistent.
[1:11] Speaker: Some gyms have saunas that never really hit the right temperature.
[1:17] Speaker: This one is legit.
[1:20] Speaker: The steam room is good too, though it's been out of service a couple times.
[1:27] Speaker: Nothing too frequent but worth noting.
[1:31] Speaker: Overall though I'm happy with the membership.
[1:35] Speaker: I've seen real results and the convenience of the location can't be beat for me.
[1:42] Speaker: Easy parking, open early, fits my schedule.
[1:47] Speaker: That's probably the most important thing honestly — it's close and accessible.
[1:54] Speaker: Which means I actually go.
[1:56] Speaker: And that's the whole battle with fitness, right?
[2:00] Speaker: Just showing up consistently.""",
            },
            "elena_r": {
                "duration": 144,
                "transcript": """\
[0:00] Speaker: Hello, I'm Elena. I just completed my first month at Iron Fitness.
[0:06] Speaker: How has your first month been?
[0:09] Speaker: Interesting. I'm still getting used to the gym honestly.
[0:14] Speaker: The size is a bit overwhelming at first.
[0:17] Speaker: It's a big space.
[0:20] Speaker: What do you think about the membership pricing?
[0:23] Speaker: It's on the higher end. I pay sixty-two dollars a month.
[0:28] Speaker: My last gym was thirty-five.
[0:32] Speaker: But I think it might be worth it once I take advantage of everything here.
[0:38] Speaker: I've only done a couple of classes so far.
[0:42] Speaker: How are the locker room facilities?
[0:45] Speaker: The women's locker room is quite nice actually.
[0:49] Speaker: It's clean and spacious and I like that there are individual changing stalls.
[0:55] Speaker: Some gyms make you change in open space which I don't love.
[1:01] Speaker: There's a nice vanity area with good lighting and hair dryers.
[1:07] Speaker: The lockers themselves are a good size.
[1:11] Speaker: Is there anything about the gym that has surprised you?
[1:14] Speaker: How social it is. People actually talk to each other.
[1:19] Speaker: At my old gym everyone had their headphones in and stared at the floor.
[1:25] Speaker: Here people say good morning, give a nod, chat a bit between sets.
[1:31] Speaker: I find that motivating actually.
[1:35] Speaker: The staff are also very approachable.
[1:39] Speaker: I had a question about signing up for a class and the front desk person walked me through everything.
[1:46] Speaker: Very patient with a new member.
[1:49] Speaker: Would you renew your membership?
[1:52] Speaker: I think so, yes. I just need to make sure I'm using it enough to justify the cost.
[1:59] Speaker: I've been going three times a week which feels good.
[2:04] Speaker: If I can keep that up then absolutely yes.""",
            },
        },
    },
    "lakeview_hotel": {
        "color": "0x8e44ad",
        "respondents": {
            "mark_s": {
                "duration": 151,
                "transcript": """\
[0:00] Speaker: Hi, I'm Mark. I stayed at The Lakeview Hotel for three nights last week.
[0:06] Speaker: How was your room?
[0:09] Speaker: Excellent. I had a lake view king room on the eighth floor.
[0:14] Speaker: The view was absolutely stunning. Woke up every morning to the sunrise over the water.
[0:21] Speaker: How was the room itself in terms of quality and comfort?
[0:25] Speaker: Very high quality. The bed was incredibly comfortable — probably the best hotel bed I've slept in.
[0:33] Speaker: The linens felt luxurious. High thread count, crisp and clean.
[0:38] Speaker: The pillows were that perfect medium firmness.
[0:43] Speaker: How about the bathroom?
[0:46] Speaker: Beautiful. Marble finishes, great water pressure in the shower.
[0:51] Speaker: The toiletries were upscale — not the little plastic bottles, they had nice ceramic dispensers.
[0:59] Speaker: The bathtub had jets, which I used every evening.
[1:05] Speaker: That was a real treat after long days of meetings.
[1:10] Speaker: Was the room quiet?
[1:13] Speaker: Yes. Barely heard anything from other rooms or the hallway.
[1:18] Speaker: I was worried because it's a busy hotel but the soundproofing is genuinely good.
[1:25] Speaker: How was the check-in experience?
[1:28] Speaker: Smooth. No wait, staff were warm and welcoming.
[1:33] Speaker: They upgraded me to a corner room which I wasn't expecting.
[1:38] Speaker: Started the stay on a great note.
[1:42] Speaker: Any aspects of the room you'd improve?
[1:45] Speaker: The closet could be larger for a longer stay.
[1:49] Speaker: And I wish there were more USB charging ports by the bed.
[1:54] Speaker: I had to unplug the lamp to charge my phone.
[1:58] Speaker: Minor things. Overall the room was exceptional.
[2:04] Speaker: I've stayed in a lot of hotels for business and this ranks near the top.
[2:11] Speaker: Would definitely book again on my next trip to the city.""",
            },
            "jessica_t": {
                "duration": 166,
                "transcript": """\
[0:00] Speaker: So the room, oh my gosh, the room was just beautiful.
[0:06] Speaker: I travel a lot for work and I have pretty high standards at this point.
[0:12] Speaker: And this room exceeded them.
[0:15] Speaker: It was so thoughtfully designed. Like someone actually thought about how a person would use the space.
[0:23] Speaker: There was great lighting throughout — not just the overhead fluorescent that makes you look sick —
[0:30] Speaker: but warm bedside lamps and vanity lighting that was actually flattering.
[0:36] Speaker: I got ready for two big client meetings in that room and felt good doing it.
[0:43] Speaker: The bed was incredible. I actually slept through my alarm one morning.
[0:48] Speaker: Which never happens to me.
[0:51] Speaker: The mattress had this perfect balance of support and softness.
[0:56] Speaker: And the blackout curtains were actually blackout. No strips of light at the edges.
[1:02] Speaker: That is so rare and so appreciated.
[1:06] Speaker: Now I do have to mention the noise situation because it was a little up and down.
[1:13] Speaker: The room itself was very quiet. Great soundproofing.
[1:18] Speaker: But on the second night there was something going on in the corridor —
[1:24] Speaker: sounded like a large group that had a bit too much to drink —
[1:29] Speaker: and that lasted until about two in the morning.
[1:34] Speaker: I called the front desk and they handled it quickly, to their credit.
[1:40] Speaker: Within about ten minutes it was resolved.
[1:43] Speaker: So staff response was good, it was just a one-time disruption.
[1:49] Speaker: The bathroom was also a highlight. Really great shower.
[1:54] Speaker: Rainfall showerhead with adjustable pressure.
[1:58] Speaker: I stood in there for a very long time.
[2:02] Speaker: No judgement. Hotel showers are one of life's pleasures.
[2:07] Speaker: Ha, exactly.
[2:09] Speaker: Overall I'd rate the room a nine out of ten.
[2:14] Speaker: Minus one for the corridor incident but that's more about other guests than the hotel itself.
[2:22] Speaker: The room itself was close to perfect.
[2:26] Speaker: I've already recommended The Lakeview to three colleagues.""",
            },
            "kevin_d": {
                "duration": 126,
                "transcript": """\
[0:00] Speaker: Sure, so I stayed here for a conference. Work put me up for two nights.
[0:06] Speaker: How was the hotel for you?
[0:09] Speaker: Good overall. Conference facilities were great, that was the main thing for me.
[0:15] Speaker: The room was comfortable. Clean, modern.
[0:20] Speaker: Nothing blew me away but nothing disappointed me either.
[0:25] Speaker: The bed was fine, slept well.
[0:28] Speaker: How about the check-in and check-out process?
[0:32] Speaker: Check-in was quick, maybe five minutes. Friendly staff.
[0:37] Speaker: Check-out was painless too, just left my key and got emailed the receipt.
[0:43] Speaker: Very smooth.
[0:46] Speaker: And you know, once I was in the conference sessions I wasn't really in my room much.
[0:53] Speaker: So the room just needed to be a place to sleep and it was perfectly fine for that.
[0:59] Speaker: But if I was on a leisure trip where I'd actually be spending time in the room —
[1:05] Speaker: I think I'd want something with a bit more character.
[1:10] Speaker: It's a nice hotel but it reads a bit corporate, if that makes sense.
[1:16] Speaker: Very clean lines, neutral colors.
[1:19] Speaker: Good for business, maybe a bit sterile for romance or vacation.
[1:25] Speaker: The restaurant in the lobby was excellent though.
[1:29] Speaker: Had breakfast there both mornings.
[1:32] Speaker: Really good eggs benedict. And the coffee was strong and good.
[1:38] Speaker: Service at breakfast was fast and friendly.
[1:42] Speaker: I'd come back for a conference without hesitation.
[1:46] Speaker: For a leisure trip I might explore other options but it'd be a close call.""",
            },
            "amy_n": {
                "duration": 144,
                "transcript": """\
[0:00] Speaker: Hi I'm Amy. I stayed here for our anniversary trip.
[0:05] Speaker: What was the highlight of your stay?
[0:08] Speaker: Definitely the rooftop bar. That's what sold us on this hotel honestly.
[0:14] Speaker: We'd seen photos on Instagram and it lived up completely.
[0:20] Speaker: The view is just spectacular. We watched the sunset from up there with cocktails.
[0:27] Speaker: I cried a little. Happy tears.
[0:30] Speaker: How were the drinks and food at the bar?
[0:33] Speaker: The cocktail menu is really creative. I had something with lavender and elderflower.
[0:40] Speaker: My husband had an old fashioned and said it was one of the best he's had.
[0:46] Speaker: We also had the charcuterie board which was generous and well curated.
[0:52] Speaker: How about the main restaurant downstairs?
[0:55] Speaker: We had dinner there on the first night. Very impressive.
[1:00] Speaker: The sea bass was cooked beautifully and the presentation was stunning.
[1:07] Speaker: Almost too pretty to eat.
[1:10] Speaker: And the sommelier helped us pick a white wine that was just perfect with the fish.
[1:17] Speaker: The service throughout dinner was impeccable.
[1:22] Speaker: Did you use any other hotel amenities?
[1:25] Speaker: We used the spa on the second afternoon. The couples massage was wonderful.
[1:32] Speaker: Very skilled therapists. I was completely relaxed afterward.
[1:37] Speaker: We also used the pool briefly. Beautiful heated outdoor pool.
[1:43] Speaker: A little chilly for extended swimming but we dipped our feet in and enjoyed the setting.
[1:50] Speaker: Any areas for improvement?
[1:53] Speaker: Maybe expand the brunch menu? We wished they had it both weekend days.
[1:59] Speaker: But that's minor. Everything was just beautiful.
[2:04] Speaker: It was the perfect anniversary trip and we'll definitely come back.""",
            },
        },
    },
    "morning_grounds_cafe": {
        "color": "0xa0522d",
        "respondents": {
            "oliver_p": {
                "duration": 115,
                "transcript": """\
[0:00] Speaker: Hey, I'm Oliver. I come here about four times a week.
[0:05] Speaker: Wow, that's a loyal customer! How's the coffee?
[0:09] Speaker: It's the reason I keep coming back. Best espresso in the neighborhood by a mile.
[0:16] Speaker: The single origin beans they rotate through are always interesting.
[0:21] Speaker: Last week it was a natural process Ethiopian and it tasted almost fruity and sweet.
[0:28] Speaker: Like drinking dessert without the sweetness, if that makes sense.
[0:33] Speaker: How's the service speed?
[0:36] Speaker: It varies. On weekday mornings the line can get long.
[0:41] Speaker: But they move quickly. Two people on the bar and one on register, well coordinated.
[0:48] Speaker: I've rarely waited more than five minutes even in line.
[0:53] Speaker: That's impressive for a specialty coffee shop.
[0:57] Speaker: Yeah, they've got the workflow dialed in.
[1:01] Speaker: How do you find the space itself?
[1:04] Speaker: Nice. Comfortable, warm. I work from here sometimes.
[1:09] Speaker: Good wifi, enough outlets.
[1:12] Speaker: It can get a bit loud when it's busy but I use headphones so it doesn't bother me much.
[1:19] Speaker: Anything you'd change?
[1:22] Speaker: More comfortable seating. Some of the chairs are a bit hard for long sessions.
[1:28] Speaker: And I wish they had a larger food menu. The pastries are great but limited.
[1:35] Speaker: Overall though it's my coffee home base. I'm not going anywhere.""",
            },
            "sophia_r": {
                "duration": 172,
                "transcript": """\
[0:00] Speaker: I want to start with the atmosphere because that's honestly why I started coming here.
[0:06] Speaker: I walked by one morning, looked in the window, and thought — yes, that is exactly where I want to be.
[0:14] Speaker: It has this warm, lived-in quality that so many coffee shops try to fake.
[0:20] Speaker: Like, the mismatched furniture. They clearly collected it over time rather than buying a set.
[0:27] Speaker: There are these old wooden tables that have been there forever, you can tell.
[0:33] Speaker: They have little scratches and marks that give them character.
[0:38] Speaker: And the walls — there are local artists' prints and paintings everywhere.
[0:44] Speaker: They rotate them which I love. Every few weeks there's new art to look at.
[0:50] Speaker: The lighting is perfect. Really warm. Those Edison bulb strings.
[0:56] Speaker: And natural light from the big windows in the front during the day.
[1:02] Speaker: You know that feeling where you just want to sink into a place?
[1:07] Speaker: That's Morning Grounds.
[1:10] Speaker: I come here to write. I'm working on a novel — been at it for two years.
[1:16] Speaker: And this is the place where I can actually get words on the page.
[1:21] Speaker: Something about the ambient noise level — not too quiet, not too loud.
[1:27] Speaker: There's always gentle background music. Not too loud, thoughtfully chosen.
[1:33] Speaker: Last week they were playing some Brazilian jazz I'd never heard before.
[1:38] Speaker: I ended up asking the barista what it was. He knew immediately.
[1:44] Speaker: That's the kind of detail that makes a place.
[1:48] Speaker: The staff are also just lovely. They know regulars by name and order.
[1:54] Speaker: I have a complicated order — oat milk cortado with a half pump of vanilla —
[2:00] Speaker: and they just start making it when they see me walk in.
[2:05] Speaker: That makes me feel so good.
[2:08] Speaker: The coffee itself is also really excellent, by the way.
[2:12] Speaker: But the atmosphere is the thing that makes me choose this over anywhere else.
[2:18] Speaker: There are closer coffee shops to my apartment.
[2:22] Speaker: I walk twenty minutes to come here. That tells you everything.
[2:28] Speaker: It just feels like a second home.
[2:32] Speaker: A really good second home where someone makes you great coffee.""",
            },
            "ben_k": {
                "duration": 118,
                "transcript": """\
[0:00] Speaker: Hi, I'm Ben. I work remotely and Morning Grounds is basically my office.
[0:07] Speaker: What do you appreciate most about working from here?
[0:11] Speaker: The wifi is reliable which is the most important thing for me practically.
[0:17] Speaker: And there are enough outlets that I can always find a seat with one nearby.
[0:23] Speaker: They've also never made me feel like I have to keep buying things to stay.
[0:29] Speaker: I buy two or three drinks throughout the day and that feels fine.
[0:35] Speaker: How's the coffee quality?
[0:38] Speaker: Honestly really good. Better than I expected when I first came in.
[0:44] Speaker: Their pour-over takes a few minutes but it's worth the wait.
[0:49] Speaker: Really clean, clear flavors. You can actually taste the coffee.
[0:54] Speaker: Not just a generic hot brown drink.
[0:58] Speaker: How do you find the atmosphere for working?
[1:02] Speaker: It works for me. Not too loud, not uncomfortably quiet.
[1:07] Speaker: I have a playlist I work to but I could probably work without it here.
[1:12] Speaker: It's just the right kind of background hum.
[1:16] Speaker: Any downsides?
[1:19] Speaker: Gets very crowded on weekend mornings. I avoid those times.
[1:24] Speaker: On weekday afternoons it's perfect.
[1:28] Speaker: And the food options are fairly limited. I end up grabbing lunch elsewhere.
[1:34] Speaker: Something more substantial would be nice.
[1:38] Speaker: But for coffee and a productive work session it's ideal.""",
            },
            "claire_m": {
                "duration": 152,
                "transcript": """\
[0:00] Speaker: What's the vibe here?
[0:02] Speaker: In one word? Cozy.
[0:05] Speaker: In more words — it feels like the kind of place that genuinely cares about the experience of being there.
[0:13] Speaker: Not just the transaction of selling you a drink.
[0:17] Speaker: How does that show up specifically?
[0:20] Speaker: The little things. Fresh flowers on the counter every single day.
[0:26] Speaker: Books on the shelves that you can actually take and read.
[0:30] Speaker: A chalkboard with a coffee fact of the week that's always interesting.
[0:36] Speaker: And the music — always something unexpected and good.
[0:41] Speaker: I've discovered so many artists because of this place.
[0:45] Speaker: I always ask what's playing and they always know.
[0:50] Speaker: It's actually become a bit of a ritual for me.
[0:54] Speaker: I come in, order my drink, ask what's on the speakers.
[0:58] Speaker: Add it to my playlist at home.
[1:02] Speaker: The atmosphere just makes you want to linger, you know?
[1:07] Speaker: There's no pressure to leave.
[1:10] Speaker: I've had three-hour visits where I just read and drank coffee and felt completely at peace.
[1:17] Speaker: But then you know it also gets really busy around eight thirty in the morning —
[1:23] Speaker: the whole pre-work rush thing —
[1:26] Speaker: and the energy shifts to something more frenetic.
[1:30] Speaker: Which is fine but different.
[1:33] Speaker: That's when it stops feeling like an oasis and starts feeling like a really efficient machine.
[1:40] Speaker: The staff are incredible under that pressure though.
[1:44] Speaker: Friendly and accurate even when it's slammed.
[1:48] Speaker: But for the pure atmosphere experience, come at ten or two.
[1:53] Speaker: That's when the place really sings.
[1:57] Speaker: And the coffee is outstanding regardless of what time you come.
[2:02] Speaker: Their espresso is genuinely some of the best I've had anywhere.
[2:07] Speaker: And I've had a lot of espresso in a lot of places.
[2:12] Speaker: This place has the whole package. Just come at the right time for the full atmosphere effect.""",
            },
            "theo_v": {
                "duration": 144,
                "transcript": """\
[0:00] Speaker: So I want to talk about the loyalty program because I have thoughts.
[0:05] Speaker: I've been coming here for two years.
[0:08] Speaker: Two years. And I have about four hundred points in the app.
[0:13] Speaker: A free drink is six hundred points.
[0:17] Speaker: Do you know how slowly that accumulates?
[0:20] Speaker: Every dollar spent is one point. My average drink is five fifty.
[0:26] Speaker: So I need to spend about six hundred dollars to get a free drink.
[0:32] Speaker: I've definitely spent more than that here. Way more.
[0:37] Speaker: My free drink should be paid for many times over at this point.
[0:42] Speaker: The system is not great.
[0:45] Speaker: And on top of that the prices have gone up twice in the past year.
[0:51] Speaker: My usual order used to be five dollars even.
[0:55] Speaker: It's now six twenty-five.
[0:58] Speaker: I get that everything costs more now but it adds up.
[1:03] Speaker: A daily coffee habit is a significant expense.
[1:07] Speaker: I've thought about switching to making my own at home.
[1:12] Speaker: I bought an espresso machine actually, a nice one.
[1:17] Speaker: But I still end up here three or four times a week anyway.
[1:22] Speaker: So I guess I'm powerless.
[1:25] Speaker: But I do think they should revamp the loyalty program.
[1:30] Speaker: Make it more rewarding for frequent customers.
[1:34] Speaker: Maybe a punch card just for the regulars, like the old fashioned kind.
[1:40] Speaker: Buy ten get one free. Simple. Effective. Appreciated.
[1:46] Speaker: Also they raised the price on almond milk add-ons to one twenty.
[1:52] Speaker: Which is just. I mean.
[1:55] Speaker: Anyway. I'll keep coming because the coffee is good.
[1:59] Speaker: But the loyalty and pricing situation is my main feedback.
[2:04] Speaker: Figure out a better system for your loyal customers please.""",
            },
        },
    },
}


def write_transcript(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def generate_video(output_path: str, duration: int, color: str) -> None:
    if Path(output_path).exists():
        return
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:size=640x480:rate=24",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            "-t", str(duration),
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def create_test_data(output_dir: str) -> None:
    base = Path(output_dir)
    for folder_name, business in BUSINESSES.items():
        folder = base / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        for filename, data in business["respondents"].items():
            txt_path = folder / f"{filename}.txt"
            mp4_path = folder / f"{filename}.mp4"
            write_transcript(str(txt_path), data["transcript"])
            generate_video(str(mp4_path), duration=data["duration"], color=business["color"])
            print(f"  {folder_name}/{filename}")


if __name__ == "__main__":
    create_test_data("test_videos")
